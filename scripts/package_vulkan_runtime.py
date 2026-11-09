#!/usr/bin/env python3
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile


def run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aar", required=True, type=Path)
    ap.add_argument("--upstream", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--cmake-cache-root", required=True, type=Path,
                    help="Search root; pass the whole upstream checkout, not only library/.cxx")
    ap.add_argument("--build-log", required=True, type=Path,
                    help="Verbose Gradle/CMake build log")
    ap.add_argument("--ndk-version", required=True)
    ap.add_argument("--spirv-headers-dir", required=True)
    ap.add_argument("--glslc", required=True)
    args = ap.parse_args()

    if not args.aar.is_file():
        raise SystemExit(f"AAR missing: {args.aar}")

    # AGP normally stores CMake state in <module>/.cxx, but its exact nested
    # layout varies by AGP version/build type. Search the whole upstream checkout.
    caches = sorted(args.cmake_cache_root.rglob("CMakeCache.txt"))
    vulkan_caches = []
    cache_summaries = []
    backend_dl_caches = []
    for cache in caches:
        cache_text = cache.read_text(encoding="utf-8", errors="replace")
        value = re.search(r"^GGML_VULKAN:[^=]+=([^\r\n]+)\s*$", cache_text, flags=re.M)
        dl_value = re.search(r"^GGML_BACKEND_DL:[^=]+=([^\r\n]+)\s*$", cache_text, flags=re.M)
        cache_summaries.append((cache, value.group(1).strip() if value else "<unset>"))
        if value and value.group(1).strip().upper() in ("ON", "1", "TRUE"):
            vulkan_caches.append((cache, cache_text))
        if dl_value and dl_value.group(1).strip().upper() in ("ON", "1", "TRUE"):
            backend_dl_caches.append((cache, cache_text))

    # Some AGP/toolchain combinations do not leave CMakeCache.txt in the
    # checkout after a successful externalNativeBuild. In that case, accept
    # independent build-output evidence instead of failing on cache location.
    build_graph_files = sorted(args.cmake_cache_root.rglob("build.ninja"))
    graph_hits = []
    for build_file in build_graph_files:
        graph = build_file.read_text(encoding="utf-8", errors="replace")
        if "ggml-vulkan" in graph.lower() or "ggml_vulkan" in graph.lower():
            graph_hits.append(build_file)

    build_log_text = args.build_log.read_text(encoding="utf-8", errors="replace") if args.build_log.is_file() else ""
    if not args.build_log.is_file():
        raise SystemExit(f"Gradle/CMake build log missing: {args.build_log}")
    gradle_file = args.upstream / "library" / "build.gradle.kts"
    gradle_text = gradle_file.read_text(encoding="utf-8", errors="replace") if gradle_file.is_file() else ""
    configured_in_gradle = bool(re.search(r"-DGGML_VULKAN\s*=\s*ON", gradle_text))
    configured_in_log = bool(re.search(r"-DGGML_VULKAN(?:=|\s+ON)", build_log_text))
    backend_dl_in_gradle = bool(re.search(r"-DGGML_BACKEND_DL\s*=\s*ON", gradle_text))
    backend_dl_in_log = bool(re.search(r"-DGGML_BACKEND_DL(?:=|\s+ON)", build_log_text))

    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    (out / "lib" / "arm64-v8a").mkdir(parents=True)
    with zipfile.ZipFile(args.aar) as zf:
        names = zf.namelist()
        if "classes.jar" not in names:
            raise SystemExit("AAR has no classes.jar")
        native = [n for n in names if n.startswith("jni/arm64-v8a/") and n.endswith(".so")]
        if not native:
            raise SystemExit("AAR has no arm64-v8a native libraries")
        if any(Path(n).name == "libvulkan.so" for n in native):
            raise SystemExit("AAR must not bundle Android's system libvulkan.so")
        basenames = {Path(n).name for n in native}
        if "libllama_jni.so" not in basenames:
            raise SystemExit("Expected upstream libllama_jni.so not found in AAR")
        if "libggml-vulkan.so" not in basenames:
            raise SystemExit(
                "AAR does not contain libggml-vulkan.so. The dynamic Vulkan backend was not "
                "packaged. Check GGML_BACKEND_DL=ON and Android Gradle externalNativeBuild packaging. "
                f"Native libraries found: {', '.join(sorted(basenames))}"
            )
        cpu_plugins = sorted(n for n in basenames if n.startswith("libggml-cpu") and n.endswith(".so"))
        if not cpu_plugins:
            raise SystemExit(
                "AAR has no dynamic ggml CPU backend (libggml-cpu*.so); CPU fallback would be unavailable. "
                f"Native libraries found: {', '.join(sorted(basenames))}"
            )
        for name in native:
            dest = out / "lib" / "arm64-v8a" / Path(name).name
            dest.write_bytes(zf.read(name))
        classes = out / "classes.jar"
        classes.write_bytes(zf.read("classes.jar"))

    so_files = sorted((out / "lib" / "arm64-v8a").glob("*.so"))
    if not so_files:
        raise SystemExit("Extracted native libs missing")
    elf_details = {}
    vulkan_needed_by = []
    for so in so_files:
        hdr = run(["readelf", "-h", str(so)])
        if "ELF64" not in hdr or "AArch64" not in hdr:
            raise SystemExit(f"Not an AArch64 ELF64 library: {so.name}")
        dynamic = run(["readelf", "-d", str(so)])
        if "libvulkan.so" in dynamic:
            vulkan_needed_by.append(so.name)
        elf_details[so.name] = {
            "sha256": sha256(so),
            "needed": re.findall(r"Shared library: \[(.*?)\]", dynamic),
            "size_bytes": so.stat().st_size,
        }

    vulkan_plugin = out / "lib" / "arm64-v8a" / "libggml-vulkan.so"
    vulkan_plugin_dynamic = run(["readelf", "-d", str(vulkan_plugin)])
    if "libvulkan.so" not in vulkan_plugin_dynamic:
        raise SystemExit(
            "libggml-vulkan.so is present but has no DT_NEEDED entry for Android system "
            "libvulkan.so. The Vulkan plugin was not linked against the NDK loader stub. "
            f"Dynamic section: {vulkan_plugin_dynamic}"
        )
    if not backend_dl_in_gradle and not backend_dl_in_log:
        raise SystemExit(
            "Dynamic backend mode is not proven: GGML_BACKEND_DL=ON was not found in the "
            "patched Gradle arguments or verbose build log."
        )

    # The target graph is required in BOTH cases: it shows that ggml-vulkan was
    # generated as part of this CMake build, not just that the option text exists.
    if not graph_hits:
        details = "; ".join(f"{p}: GGML_VULKAN={v}" for p, v in cache_summaries) or "no CMakeCache.txt files found"
        raise SystemExit(
            "No Ninja build graph containing ggml-vulkan was found under "
            f"{args.cmake_cache_root}. CMake cache scan: {details}. "
            "The native Vulkan target may not have been configured/built."
        )

    # Preferred evidence is the generated CMake cache. If AGP did not leave it,
    # require the patched CMake argument, Ninja target graph, and final ELF link.
    if vulkan_caches:
        if not backend_dl_caches:
            raise SystemExit(
                "CMake cache confirms GGML_VULKAN=ON but no cache confirms GGML_BACKEND_DL=ON; "
                "this project requires the separate libggml-vulkan.so plugin."
            )
        verification_mode = "CMakeCache(Vulkan+backend-DL) + Ninja graph + Vulkan-plugin ELF DT_NEEDED"
    else:
        if not configured_in_gradle and not configured_in_log:
            details = "; ".join(f"{p}: GGML_VULKAN={v}" for p, v in cache_summaries) or "no CMakeCache.txt files found"
            raise SystemExit(
                "CMakeCache with GGML_VULKAN=ON was not found, and the verbose build log "
                "does not show the option. The Ninja graph and ELF link alone are not "
                f"enough to prove the requested configuration. Cache scan: {details}."
            )
        verification_mode = "Gradle CMake argument + Ninja ggml-vulkan graph + ELF DT_NEEDED (AGP cache absent)"

    # The upstream JNI wrapper must retain the original Kotlin API.
    jar_listing = run(["jar", "tf", str(out / "classes.jar")])
    for required in ("dev/ffmpegkit/llama/Llama.class", "dev/ffmpegkit/llama/LlamaConfig.class"):
        if required not in jar_listing:
            raise SystemExit(f"AAR Kotlin API check failed: {required} missing")

    upstream_sha = run(["git", "-C", str(args.upstream), "rev-parse", "HEAD"]).strip()
    submodule_sha = run(["git", "-C", str(args.upstream / "llama.cpp"), "rev-parse", "HEAD"]).strip()
    submodule_desc = run(["git", "-C", str(args.upstream / "llama.cpp"), "describe", "--tags", "--always"]).strip()
    manifest = {
        "artifact": "llama-android Vulkan-enabled native runtime",
        "abi": "arm64-v8a",
        "ndk_version": args.ndk_version,
        "upstream_repository": "https://github.com/ffmpegkit-maintained/llama-android",
        "upstream_commit": upstream_sha,
        "llama_cpp_submodule_commit": submodule_sha,
        "llama_cpp_submodule_describe": submodule_desc,
        "cmake_options_required": ["GGML_VULKAN=ON", "GGML_BACKEND_DL=ON", "BUILD_SHARED_LIBS=OFF"],
        "vulkan_include_dir": "Android NDK sysroot",
        "vulkan_loader_library": "Android NDK system Vulkan stub (not bundled)",
        "glslc": args.glslc,
        "spirv_headers_dir": args.spirv_headers_dir,
        "verification_mode": verification_mode,
        "cmake_cache_files_scanned": [str(p) for p, _ in cache_summaries],
        "cmake_caches_with_vulkan_on": [str(p) for p, _ in vulkan_caches],
        "cmake_caches_with_backend_dl_on": [str(p) for p, _ in backend_dl_caches],
        "ninja_graphs_with_ggml_vulkan": [str(p) for p in graph_hits],
        "gradle_argument_GGML_VULKAN_ON": configured_in_gradle,
        "build_log_mentions_GGML_VULKAN_ON": configured_in_log,
        "gradle_argument_GGML_BACKEND_DL_ON": backend_dl_in_gradle,
        "build_log_mentions_GGML_BACKEND_DL_ON": backend_dl_in_log,
        "native_libraries": elf_details,
        "vulkan_dt_needed_by": vulkan_needed_by,
        "aar": {"name": args.aar.name, "sha256": sha256(args.aar), "size_bytes": args.aar.stat().st_size},
    }
    (out / "BUILD_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    shutil.copy2(args.aar, out / "llama-android-vulkan.aar")
    license_path = args.upstream / "LICENSE"
    if license_path.exists():
        shutil.copy2(license_path, out / "UPSTREAM-LICENSE")
    (out / "README_RUNTIME_RU.txt").write_text(
        "Hy-MT2 Vulkan Native Runtime\n"
        "ABI: arm64-v8a\n"
        "Kotlin API: dev.ffmpegkit.llama (upstream-compatible)\n"
        "Vulkan backend: GGML_VULKAN=ON; GGML_BACKEND_DL=ON (dynamic module)\n"
        "Android loader: system libvulkan.so (not bundled)\n"
        "Native files: lib/arm64-v8a/*.so\n"
        "For the full build identity and SHA-256 values, see BUILD_MANIFEST.json.\n",
        encoding="utf-8",
    )
    # The archive is the drop-in native bundle plus the exact AAR.
    archive = out.parent / "HyMT2-Vulkan-NativeRuntime.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(out.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(out.parent))
    print(f"Verified AAR: {args.aar}")
    print(f"Native libraries: {len(so_files)}")
    print(f"Vulkan plugin: {vulkan_plugin.name} -> DT_NEEDED libvulkan.so")
    print(f"CPU backend plugin(s): {', '.join(cpu_plugins)}")
    print(f"Runtime bundle: {archive}")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stdout or str(exc), file=sys.stderr)
        raise
