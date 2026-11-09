#!/usr/bin/env python3
"""Verify/package an Android llama.cpp Vulkan runtime from an AAR build.

This recipe uses regular shared ggml backends (GGML_BACKEND_DL=OFF,
BUILD_SHARED_LIBS=ON). Android's dynamic linker loads the backends through the
normal DT_NEEDED chain; no backend directory scan or app_process path guess is
required. AGP may omit CMake dependency targets of type SHARED from an AAR's
jni/ directory, so this script adds the exact ARM64 backend .so files from the
same CMake build when they are missing, then validates the ELF dependency chain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
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


def is_arm64_elf(path: Path) -> bool:
    try:
        hdr = run(["readelf", "-h", str(path)])
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return "ELF64" in hdr and "AArch64" in hdr


def needed(path: Path) -> list[str]:
    dynamic = run(["readelf", "-d", str(path)])
    return re.findall(r"Shared library: \[(.*?)\]", dynamic)


def find_built_backend(root: Path, pattern: str) -> Path | None:
    candidates = [p for p in root.rglob(pattern) if p.is_file() and is_arm64_elf(p)]
    if not candidates:
        return None
    # Prefer AGP's per-ABI output folders, then the CMake runtime output folder.
    def score(p: Path) -> tuple[int, int, float]:
        s = str(p).replace("\\", "/")
        path_score = 2 if "arm64-v8a" in s else 0
        path_score += 1 if "/bin/" in s else 0
        return (path_score, p.stat().st_size, p.stat().st_mtime)
    candidates.sort(key=score, reverse=True)
    return candidates[0]


def patch_aar_with_build_backends(aar: Path, upstream: Path) -> list[str]:
    """Add missing regular shared backend libraries to jni/arm64-v8a in the AAR."""
    with zipfile.ZipFile(aar, "r") as src:
        names = set(src.namelist())
        native_names = {
            Path(name).name for name in names
            if name.startswith("jni/arm64-v8a/") and name.endswith(".so")
        }
        missing_names: list[str] = []
        if "libggml-vulkan.so" not in native_names:
            missing_names.append("libggml-vulkan.so")
        if not any(n.startswith("libggml-cpu") and n.endswith(".so") for n in native_names):
            missing_names.append("<CPU-backend>")

        additions: dict[str, bytes] = {}
        for name in missing_names:
            if name == "<CPU-backend>":
                candidates = [
                    p for p in upstream.rglob("libggml-cpu*.so")
                    if p.is_file() and is_arm64_elf(p)
                ]
                if not candidates:
                    raise SystemExit(
                        f"AAR has no shared CPU backend and no AArch64 libggml-cpu*.so build output was found under {upstream}."
                    )
                candidates.sort(
                    key=lambda p: ("arm64-v8a" in str(p), p.stat().st_size, p.stat().st_mtime),
                    reverse=True,
                )
                candidate = candidates[0]
            else:
                candidate = find_built_backend(upstream, name)
                if candidate is None:
                    raise SystemExit(
                        f"AAR is missing {name}, and no AArch64 build output with that name was found under {upstream}. "
                        "This means the backend target was not built; packaging cannot repair it."
                    )
            additions[f"jni/arm64-v8a/{candidate.name}"] = candidate.read_bytes()

        if not additions:
            return []

        patched = aar.with_suffix(".with-backends.aar")
        with zipfile.ZipFile(patched, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as dst:
            for info in src.infolist():
                dst.writestr(info, src.read(info.filename))
            for arcname, data in additions.items():
                if arcname not in names:
                    dst.writestr(arcname, data)
    patched.replace(aar)
    return sorted(additions)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aar", required=True, type=Path)
    ap.add_argument("--upstream", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--cmake-cache-root", required=True, type=Path)
    ap.add_argument("--build-log", required=True, type=Path)
    ap.add_argument("--ndk-version", required=True)
    ap.add_argument("--spirv-headers-dir", required=True)
    ap.add_argument("--glslc", required=True)
    args = ap.parse_args()

    if not args.aar.is_file():
        raise SystemExit(f"AAR missing: {args.aar}")
    if not args.build_log.is_file():
        raise SystemExit(f"Gradle/CMake build log missing: {args.build_log}")

    cache_root = args.cmake_cache_root
    caches = sorted(cache_root.rglob("CMakeCache.txt"))
    cache_records: list[dict[str, str]] = []
    cache_good = []
    for cache in caches:
        t = cache.read_text(encoding="utf-8", errors="replace")
        vals = {}
        for key in ("GGML_VULKAN", "GGML_BACKEND_DL", "BUILD_SHARED_LIBS"):
            m = re.search(rf"^{re.escape(key)}:[^=]+=([^\r\n]+)\s*$", t, re.M)
            vals[key] = m.group(1).strip().upper() if m else "<unset>"
        cache_records.append({"path": str(cache), **vals})
        if vals == {"GGML_VULKAN": "ON", "GGML_BACKEND_DL": "OFF", "BUILD_SHARED_LIBS": "ON"}:
            cache_good.append(cache)

    graphs = sorted(cache_root.rglob("build.ninja"))
    graph_hits = []
    for graph_file in graphs:
        graph = graph_file.read_text(encoding="utf-8", errors="replace")
        if "ggml-vulkan" in graph.lower():
            graph_hits.append(graph_file)

    gradle_file = args.upstream / "library" / "build.gradle.kts"
    gradle_text = gradle_file.read_text(encoding="utf-8", errors="replace") if gradle_file.is_file() else ""
    build_log = args.build_log.read_text(encoding="utf-8", errors="replace")

    def configured(key: str, value: str) -> tuple[bool, bool]:
        pat = rf"-D{re.escape(key)}\s*=\s*{re.escape(value)}"
        return bool(re.search(pat, gradle_text, re.I)), bool(re.search(pat, build_log, re.I))

    gradle_vk, log_vk = configured("GGML_VULKAN", "ON")
    gradle_dl, log_dl = configured("GGML_BACKEND_DL", "OFF")
    gradle_shared, log_shared = configured("BUILD_SHARED_LIBS", "ON")
    configs_from_text = (gradle_vk or log_vk) and (gradle_dl or log_dl) and (gradle_shared or log_shared)

    # Require the actual generated graph even when AGP omits CMakeCache artifacts.
    if not graph_hits:
        raise SystemExit(
            f"No Ninja graph containing ggml-vulkan was found under {cache_root}. "
            f"CMake caches: {cache_records or 'none'}. Vulkan backend target was not configured or generated."
        )
    if cache_good:
        verification_mode = "CMakeCache(GGML_VULKAN=ON, BACKEND_DL=OFF, BUILD_SHARED_LIBS=ON) + Ninja graph + ELF chain"
    else:
        if not configs_from_text:
            raise SystemExit(
                "No CMakeCache proves the required shared-link configuration, and Gradle/build log do not "
                "show all three options: GGML_VULKAN=ON, GGML_BACKEND_DL=OFF, BUILD_SHARED_LIBS=ON. "
                f"CMake cache scan: {cache_records or 'no CMakeCache.txt files found'}"
            )
        verification_mode = "Gradle/build-log configuration + Ninja graph + ELF chain (AGP cache absent)"

    # The CMake build output is the source of truth. AGP does not always include
    # dependency SHARED targets in a library AAR, so copy the same-build outputs
    # into the canonical jni/arm64-v8a AAR path before validation.
    added = patch_aar_with_build_backends(args.aar, args.upstream)

    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    native_out = out / "lib" / "arm64-v8a"
    native_out.mkdir(parents=True)
    with zipfile.ZipFile(args.aar, "r") as zf:
        names = zf.namelist()
        if "classes.jar" not in names:
            raise SystemExit("AAR has no classes.jar")
        native = [n for n in names if n.startswith("jni/arm64-v8a/") and n.endswith(".so")]
        if not native:
            raise SystemExit("AAR has no arm64-v8a native libraries")
        if any(Path(n).name == "libvulkan.so" for n in native):
            raise SystemExit("AAR must not bundle Android's system libvulkan.so")
        basenames = {Path(n).name for n in native}
        for required in ("libllama_jni.so", "libllama.so", "libggml.so", "libggml-base.so", "libggml-vulkan.so"):
            if required not in basenames:
                raise SystemExit(f"AAR missing required ARM64 library {required}; found {', '.join(sorted(basenames))}")
        cpu_libs = sorted(n for n in basenames if n.startswith("libggml-cpu") and n.endswith(".so"))
        if not cpu_libs:
            raise SystemExit(f"AAR has no shared CPU backend; found {', '.join(sorted(basenames))}")
        for name in native:
            zf.extract(name, out / "aar-extract")
            (native_out / Path(name).name).write_bytes((out / "aar-extract" / name).read_bytes())
        (out / "classes.jar").write_bytes(zf.read("classes.jar"))

    so_files = sorted(native_out.glob("*.so"))
    elf_details = {}
    for so in so_files:
        hdr = run(["readelf", "-h", str(so)])
        if "ELF64" not in hdr or "AArch64" not in hdr:
            raise SystemExit(f"Not an AArch64 ELF64 library: {so.name}")
        elf_details[so.name] = {"sha256": sha256(so), "needed": needed(so), "size_bytes": so.stat().st_size}

    vulkan = native_out / "libggml-vulkan.so"
    vulkan_needed = needed(vulkan)
    if "libvulkan.so" not in vulkan_needed:
        raise SystemExit(
            "libggml-vulkan.so was packaged but is not linked to Android system libvulkan.so. "
            f"DT_NEEDED={vulkan_needed}"
        )
    ggml_needed = needed(native_out / "libggml.so")
    if "libggml-vulkan.so" not in ggml_needed:
        raise SystemExit(
            "libggml.so does not link libggml-vulkan.so; Vulkan backend may be present but is not registered/linked. "
            f"DT_NEEDED={ggml_needed}"
        )
    if not any(name.startswith("libggml-cpu") and name.endswith(".so") for name in ggml_needed):
        raise SystemExit(
            "libggml.so does not link the shared CPU backend; CPU fallback chain is not proven. "
            f"DT_NEEDED={ggml_needed}"
        )

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
        "cmake_options_required": ["GGML_VULKAN=ON", "GGML_BACKEND_DL=OFF", "BUILD_SHARED_LIBS=ON"],
        "backend_link_mode": "regular shared libraries linked through libggml.so; no plugin dlopen search",
        "vulkan_include_dir": "Android NDK sysroot",
        "vulkan_loader_library": "Android NDK system Vulkan stub (not bundled)",
        "glslc": args.glslc,
        "spirv_headers_dir": args.spirv_headers_dir,
        "verification_mode": verification_mode,
        "cmake_cache_records": cache_records,
        "ninja_graphs_with_ggml_vulkan": [str(p) for p in graph_hits],
        "gradle_argument_GGML_VULKAN_ON": gradle_vk,
        "build_log_mentions_GGML_VULKAN_ON": log_vk,
        "gradle_argument_GGML_BACKEND_DL_OFF": gradle_dl,
        "build_log_mentions_GGML_BACKEND_DL_OFF": log_dl,
        "gradle_argument_BUILD_SHARED_LIBS_ON": gradle_shared,
        "build_log_mentions_BUILD_SHARED_LIBS_ON": log_shared,
        "backend_libraries_injected_into_aar_from_same_build": added,
        "libggml_dt_needed": ggml_needed,
        "libggml_vulkan_dt_needed": vulkan_needed,
        "native_libraries": elf_details,
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
        "Vulkan: GGML_VULKAN=ON\n"
        "Link mode: BUILD_SHARED_LIBS=ON, GGML_BACKEND_DL=OFF\n"
        "libggml.so links libggml-vulkan.so and libggml-cpu.so\n"
        "Android system libvulkan.so is not bundled\n"
        "For build identity and SHA-256 values, see BUILD_MANIFEST.json.\n",
        encoding="utf-8",
    )

    archive = out.parent / "HyMT2-Vulkan-NativeRuntime.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(out.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(out.parent))

    print(f"Verified AAR: {args.aar}")
    print(f"Native libraries: {len(so_files)}")
    print("Vulkan shared backend: libggml-vulkan.so -> DT_NEEDED libvulkan.so")
    print(f"CPU shared backend(s): {', '.join(cpu_libs)}")
    print(f"Backend libraries added to AAR by post-build packaging: {', '.join(added) if added else 'none; AGP already packaged them'}")
    print(f"Runtime bundle: {archive}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stdout or str(exc), file=sys.stderr)
        raise
