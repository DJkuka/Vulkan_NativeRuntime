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
    ap.add_argument("--cmake-cache-root", required=True, type=Path)
    ap.add_argument("--ndk-version", required=True)
    ap.add_argument("--spirv-headers-dir", required=True)
    ap.add_argument("--glslc", required=True)
    args = ap.parse_args()

    if not args.aar.is_file():
        raise SystemExit(f"AAR missing: {args.aar}")

    caches = list(args.cmake_cache_root.rglob("CMakeCache.txt"))
    vulkan_caches = []
    for cache in caches:
        text = cache.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^GGML_VULKAN:BOOL=ON$", text, flags=re.M):
            vulkan_caches.append((cache, text))
    if not vulkan_caches:
        raise SystemExit("No CMakeCache.txt with GGML_VULKAN:BOOL=ON was found")

    build_graph_files = list(args.cmake_cache_root.rglob("build.ninja"))
    graph_hits = []
    for build_file in build_graph_files:
        graph = build_file.read_text(encoding="utf-8", errors="replace")
        if "ggml-vulkan" in graph.lower() or "ggml_vulkan" in graph.lower():
            graph_hits.append(build_file)
    if not graph_hits:
        raise SystemExit("CMake/Ninja build graph does not show ggml-vulkan target")

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
        if not any(Path(n).name == "libllama_jni.so" for n in native):
            raise SystemExit("Expected upstream libllama_jni.so not found in AAR")
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

    if not vulkan_needed_by:
        raise SystemExit("No extracted native library has DT_NEEDED for Android system libvulkan.so")

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
        "cmake_options_required": ["GGML_VULKAN=ON", "GGML_BACKEND_DL=OFF", "BUILD_SHARED_LIBS=OFF"],
        "vulkan_include_dir": "Android NDK sysroot",
        "vulkan_loader_library": "Android NDK system Vulkan stub (not bundled)",
        "glslc": args.glslc,
        "spirv_headers_dir": args.spirv_headers_dir,
        "cmake_caches_with_vulkan_on": [str(p) for p, _ in vulkan_caches],
        "ninja_graphs_with_ggml_vulkan": [str(p) for p in graph_hits],
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
        "Vulkan backend: GGML_VULKAN=ON; GGML_BACKEND_DL=OFF\n"
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
    print(f"DT_NEEDED libvulkan.so: {', '.join(vulkan_needed_by)}")
    print(f"Runtime bundle: {archive}")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stdout or str(exc), file=sys.stderr)
        raise
