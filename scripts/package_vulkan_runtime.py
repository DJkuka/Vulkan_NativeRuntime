#!/usr/bin/env python3
"""Verify and package the Android llama.cpp Vulkan AAR.

Configuration: GGML_VULKAN=ON, GGML_CPU=ON, GGML_BACKEND_DL=OFF,
BUILD_SHARED_LIBS=ON. In this combination ggml-vulkan and ggml-cpu are regular
SHARED libraries (not MODULE/dlopen plugins) and are linked into libggml.so
through normal DT_NEEDED dependencies. Android system libvulkan.so is not
bundled; the Vulkan backend must depend on it at runtime.

AGP may omit dependency shared objects from the AAR. If so, this script injects
the exact ARM64 .so files produced by the same CMake build, then verifies the
ELF architecture and the complete DT_NEEDED chain.
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
import zipfile

REQUIRED_CACHE = {
    "GGML_VULKAN": "ON",
    "GGML_CPU": "ON",
    "GGML_BACKEND_DL": "OFF",
    "BUILD_SHARED_LIBS": "ON",
}


def run(cmd: list[str]) -> str:
    result = subprocess.run(
        cmd, check=True, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.stdout


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_arm64_elf(path: Path) -> bool:
    try:
        header = run(["readelf", "-h", str(path)])
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return "ELF64" in header and "AArch64" in header


def needed(path: Path) -> list[str]:
    dynamic = run(["readelf", "-d", str(path)])
    return re.findall(r"Shared library: \[(.*?)\]", dynamic)


def parse_cache_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in REQUIRED_CACHE:
        match = re.search(rf"^{re.escape(key)}:[^=]+=([^\r\n]+)\s*$", text, re.M)
        values[key] = match.group(1).strip().upper() if match else "<unset>"
    return values


def graph_has_backend(graph_text: str, backend: str) -> bool:
    return backend.lower() in graph_text.lower()


def find_built_backend(root: Path, filename_pattern: str) -> Path | None:
    candidates = [
        path for path in root.rglob(filename_pattern)
        if path.is_file() and is_arm64_elf(path)
    ]
    if not candidates:
        return None

    def score(path: Path) -> tuple[int, int, float]:
        value = str(path).replace("\\", "/")
        path_score = 2 if "arm64-v8a" in value else 0
        path_score += 1 if "/bin/" in value else 0
        return path_score, path.stat().st_size, path.stat().st_mtime

    candidates.sort(key=score, reverse=True)
    return candidates[0]


def patch_aar_with_build_backends(aar: Path, upstream: Path) -> list[str]:
    """Inject missing linked backend .so dependencies from this exact ARM64 build."""
    with zipfile.ZipFile(aar, "r") as source:
        names = set(source.namelist())
        native_names = {
            Path(name).name for name in names
            if name.startswith("jni/arm64-v8a/") and name.endswith(".so")
        }

        additions: dict[str, bytes] = {}

        if "libggml-vulkan.so" not in native_names:
            candidate = find_built_backend(upstream, "libggml-vulkan.so")
            if candidate is None:
                raise SystemExit(
                    "AAR is missing libggml-vulkan.so and no AArch64 build output with that name "
                    f"was found under {upstream}. The Vulkan target may not have been compiled."
                )
            additions[f"jni/arm64-v8a/{candidate.name}"] = candidate.read_bytes()

        if not any(name.startswith("libggml-cpu") and name.endswith(".so") for name in native_names):
            candidate = find_built_backend(upstream, "libggml-cpu*.so")
            if candidate is None:
                raise SystemExit(
                    "AAR has no shared CPU backend and no AArch64 libggml-cpu*.so build output "
                    f"was found under {upstream}. CPU fallback was not built."
                )
            additions[f"jni/arm64-v8a/{candidate.name}"] = candidate.read_bytes()

        if not additions:
            return []

        temporary = aar.with_suffix(".with-backends.aar")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
            for info in source.infolist():
                target.writestr(info, source.read(info.filename))
            for entry, data in additions.items():
                if entry not in names:
                    target.writestr(entry, data)
        temporary.replace(aar)
        return sorted(additions)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aar", required=True, type=Path)
    parser.add_argument("--upstream", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--cmake-cache-root", required=True, type=Path)
    parser.add_argument("--build-log", required=True, type=Path)
    parser.add_argument("--ndk-version", required=True)
    parser.add_argument("--spirv-headers-dir", required=True)
    parser.add_argument("--spirv-headers-include-dir", required=True, type=Path)
    parser.add_argument("--spirv-headers-commit", required=True)
    parser.add_argument("--vulkan-include-dir", required=True)
    parser.add_argument("--vulkan-headers-commit", required=True)
    parser.add_argument("--glslc", required=True)
    args = parser.parse_args()

    if not args.aar.is_file():
        raise SystemExit(f"AAR missing: {args.aar}")
    if not args.build_log.is_file():
        raise SystemExit(f"Gradle/CMake build log missing: {args.build_log}")

    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.vulkan_headers_commit):
        raise SystemExit(f"Invalid Vulkan-Headers commit SHA: {args.vulkan_headers_commit!r}")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.spirv_headers_commit):
        raise SystemExit(f"Invalid SPIRV-Headers commit SHA: {args.spirv_headers_commit!r}")
    vulkan_include_dir = args.vulkan_include_dir.resolve()
    spirv_headers_include_dir = args.spirv_headers_include_dir.resolve()
    if str(spirv_headers_include_dir) == "/usr/include" or str(spirv_headers_include_dir).startswith("/usr/include/"):
        raise SystemExit(
            "SPIRV-Headers must be staged under RUNNER_TEMP, not /usr/include. "
            "CMake may treat /usr/include as an implicit host path and omit it "
            "from Android cross-compile include flags."
        )
    if not (spirv_headers_include_dir / "spirv/unified1/spirv.hpp").is_file():
        raise SystemExit(
            "SPIRV-Headers include directory is missing spirv/unified1/spirv.hpp: "
            f"{spirv_headers_include_dir}"
        )
    for required_header in ("vulkan/vulkan.h", "vulkan/vulkan.hpp", "vulkan/vulkan_core.h"):
        if not (vulkan_include_dir / required_header).is_file():
            raise SystemExit(
                f"Khronos Vulkan-Headers is incomplete: missing {vulkan_include_dir / required_header}"
            )

    cache_root = args.cmake_cache_root
    caches = sorted(cache_root.rglob("CMakeCache.txt"))
    cache_records: list[dict[str, str]] = []
    cache_good: list[Path] = []
    for cache in caches:
        cache_text = cache.read_text(encoding="utf-8", errors="replace")
        values = parse_cache_values(cache_text)
        include_match = re.search(r"^Vulkan_INCLUDE_DIR:[^=]+=([^\r\n]+)\s*$", cache_text, re.M)
        values["Vulkan_INCLUDE_DIR"] = include_match.group(1).strip() if include_match else "<unset>"
        spirv_include_match = re.search(r"^SPIRV_HEADERS_INCLUDE_DIR:[^=]+=([^\r\n]+)\s*$", cache_text, re.M)
        values["SPIRV_HEADERS_INCLUDE_DIR"] = spirv_include_match.group(1).strip() if spirv_include_match else "<unset>"
        cache_records.append({"path": str(cache), **values})
        if (
            values["Vulkan_INCLUDE_DIR"] == str(vulkan_include_dir)
            and values["SPIRV_HEADERS_INCLUDE_DIR"] == str(spirv_headers_include_dir)
            and all(values.get(key) == val for key, val in REQUIRED_CACHE.items())
        ):
            cache_good.append(cache)

    graphs = sorted(cache_root.rglob("build.ninja"))
    graph_hits: list[Path] = []
    cpu_graph_hits: list[Path] = []
    for graph_file in graphs:
        graph_text = graph_file.read_text(encoding="utf-8", errors="replace")
        if graph_has_backend(graph_text, "ggml-vulkan"):
            graph_hits.append(graph_file)
        if graph_has_backend(graph_text, "ggml-cpu"):
            cpu_graph_hits.append(graph_file)

    gradle_file = args.upstream / "library" / "build.gradle.kts"
    cmake_file = args.upstream / "library" / "src" / "main" / "jni" / "CMakeLists.txt"
    gradle_text = gradle_file.read_text(encoding="utf-8", errors="replace") if gradle_file.is_file() else ""
    cmake_text = cmake_file.read_text(encoding="utf-8", errors="replace") if cmake_file.is_file() else ""
    build_log = args.build_log.read_text(encoding="utf-8", errors="replace")

    def configured(key: str, value: str) -> tuple[bool, bool, bool]:
        pattern = rf"-D{re.escape(key)}\s*=\s*{re.escape(value)}"
        force_pattern = rf"set\(\s*{re.escape(key)}\s+{re.escape(value)}\s+CACHE\s+BOOL[^)]*FORCE"
        return (
            bool(re.search(pattern, gradle_text, re.I)),
            bool(re.search(pattern, build_log, re.I)),
            bool(re.search(force_pattern, cmake_text, re.I | re.S)),
        )

    text_flags = {key: configured(key, value) for key, value in REQUIRED_CACHE.items()}
    text_config_ok = all(any(flag_tuple) for flag_tuple in text_flags.values())

    # A real CMakeCache is authoritative. Never accept Gradle text if it says OFF.
    if caches and not cache_good:
        raise SystemExit(
            "CMakeCache exists but none has the required effective values and Khronos Vulkan-Hpp include path "
            "(GGML_VULKAN=ON, GGML_CPU=ON, GGML_BACKEND_DL=OFF, BUILD_SHARED_LIBS=ON, "
            f"Vulkan_INCLUDE_DIR={vulkan_include_dir}, "
            f"SPIRV_HEADERS_INCLUDE_DIR={spirv_headers_include_dir}). Observed caches: {cache_records}. "
            "Check that the JNI CMake explicitly propagates both Vulkan-Headers and SPIRV-Headers include roots."
        )
    if not caches and not text_config_ok:
        raise SystemExit(
            "No CMakeCache was generated, and Gradle/JNI CMake/build-log evidence does not prove "
            f"all required values. Flags (Gradle, build log, JNI CMake FORCE): {text_flags}"
        )
    if not graph_hits:
        raise SystemExit(
            f"No Ninja graph containing ggml-vulkan was found under {cache_root}. "
            f"CMake caches: {cache_records or 'none'}. Vulkan target was not generated."
        )
    if not cpu_graph_hits:
        raise SystemExit(
            f"No Ninja graph containing ggml-cpu was found under {cache_root}. "
            f"CMake caches: {cache_records or 'none'}. CPU fallback target was not generated."
        )

    verification_mode = (
        "effective CMakeCache + Vulkan/CPU Ninja targets + ARM64 ELF DT_NEEDED"
        if cache_good else
        "Gradle/JNI CMake/build-log evidence + Vulkan/CPU Ninja targets + ARM64 ELF DT_NEEDED (cache unavailable)"
    )

    # Backend targets are ordinary SHARED deps in this mode (not MODULE/dlopen).
    added = patch_aar_with_build_backends(args.aar, args.upstream)

    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    native_out = out / "lib" / "arm64-v8a"
    native_out.mkdir(parents=True)

    with zipfile.ZipFile(args.aar, "r") as archive:
        names = archive.namelist()
        if "classes.jar" not in names:
            raise SystemExit("AAR has no classes.jar")
        native_entries = [
            name for name in names
            if name.startswith("jni/arm64-v8a/") and name.endswith(".so")
        ]
        if not native_entries:
            raise SystemExit("AAR has no arm64-v8a native libraries")
        if any(Path(name).name == "libvulkan.so" for name in native_entries):
            raise SystemExit("AAR must not bundle Android's system libvulkan.so")
        basenames = {Path(name).name for name in native_entries}
        required_native = {"libllama_jni.so", "libllama.so", "libggml.so", "libggml-base.so", "libggml-vulkan.so"}
        missing = sorted(required_native - basenames)
        if missing:
            raise SystemExit(f"AAR missing ARM64 libraries {missing}; found {sorted(basenames)}")
        cpu_libs = sorted(name for name in basenames if name.startswith("libggml-cpu") and name.endswith(".so"))
        if not cpu_libs:
            raise SystemExit(f"AAR has no shared CPU backend; found {sorted(basenames)}")
        for name in native_entries:
            archive.extract(name, out / "aar-extract")
            (native_out / Path(name).name).write_bytes(
                (out / "aar-extract" / name).read_bytes()
            )
        (out / "classes.jar").write_bytes(archive.read("classes.jar"))

    so_files = sorted(native_out.glob("*.so"))
    elf_details: dict[str, dict[str, object]] = {}
    for library in so_files:
        header = run(["readelf", "-h", str(library)])
        if "ELF64" not in header or "AArch64" not in header:
            raise SystemExit(f"Not an AArch64 ELF64 library: {library.name}")
        elf_details[library.name] = {
            "sha256": sha256(library),
            "needed": needed(library),
            "size_bytes": library.stat().st_size,
        }

    dependencies = {name: needed(native_out / name) for name in basenames if name.endswith(".so")}
    ggml_needed = dependencies.get("libggml.so", [])
    vulkan_needed = dependencies.get("libggml-vulkan.so", [])
    if "libggml-vulkan.so" not in ggml_needed:
        raise SystemExit(
            "libggml.so does not link the Vulkan shared backend. "
            f"DT_NEEDED={ggml_needed}."
        )
    if "libvulkan.so" not in vulkan_needed:
        raise SystemExit(
            "libggml-vulkan.so is not linked to Android system libvulkan.so. "
            f"DT_NEEDED={vulkan_needed}."
        )
    if not any(cpu_name in ggml_needed for cpu_name in cpu_libs):
        raise SystemExit(
            "libggml.so does not link any shared CPU backend; CPU fallback chain is not proven. "
            f"libggml.so DT_NEEDED={ggml_needed}; CPU libs={cpu_libs}."
        )

    jar_listing = run(["jar", "tf", str(out / "classes.jar")])
    for required_class in (
        "dev/ffmpegkit/llama/Llama.class",
        "dev/ffmpegkit/llama/LlamaConfig.class",
    ):
        if required_class not in jar_listing:
            raise SystemExit(f"AAR Kotlin API check failed: {required_class} missing")

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
        "cmake_options_required": REQUIRED_CACHE,
        "backend_link_mode": "ordinary shared backend dependencies linked by libggml.so; GGML_BACKEND_DL=OFF, no dlopen plugin scan",
        "vulkan_include_dir": str(vulkan_include_dir),
        "vulkan_headers_commit": args.vulkan_headers_commit,
        "vulkan_cpp_header": str(vulkan_include_dir / "vulkan" / "vulkan.hpp"),
        "spirv_headers_commit": args.spirv_headers_commit,
        "spirv_headers_include_dir": str(spirv_headers_include_dir),
        "spirv_cpp_header": str(spirv_headers_include_dir / "spirv" / "unified1" / "spirv.hpp"),
        "vulkan_loader_library": "Android NDK system Vulkan stub (not bundled)",
        "glslc": args.glslc,
        "spirv_headers_dir": args.spirv_headers_dir,
        "verification_mode": verification_mode,
        "cmake_cache_records": cache_records,
        "ninja_graphs_with_ggml_vulkan": [str(path) for path in graph_hits],
        "ninja_graphs_with_ggml_cpu": [str(path) for path in cpu_graph_hits],
        "gradle_jni_cmake_build_log_flags": {
            key: {"gradle": flags[0], "build_log": flags[1], "jni_cmake_force": flags[2]}
            for key, flags in text_flags.items()
        },
        "backend_libraries_injected_from_same_build": added,
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
        "CPU fallback: GGML_CPU=ON\n"
        "Link mode: BUILD_SHARED_LIBS=ON, GGML_BACKEND_DL=OFF\n"
        "libggml.so links ordinary shared libggml-vulkan.so and libggml-cpu*.so dependencies\n"
        "libggml-vulkan.so links Android system libvulkan.so (not bundled)\n"
        "For build identity and SHA-256 values, see BUILD_MANIFEST.json.\n",
        encoding="utf-8",
    )

    runtime_zip = out.parent / "HyMT2-Vulkan-NativeRuntime.zip"
    if runtime_zip.exists():
        runtime_zip.unlink()
    with zipfile.ZipFile(runtime_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(out.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(out.parent))

    print(f"Verified AAR: {args.aar}")
    print(f"Native libraries: {len(so_files)}")
    print("Vulkan link: libggml.so -> libggml-vulkan.so -> libvulkan.so")
    print(f"CPU fallback shared backend(s): {', '.join(cpu_libs)}")
    print(f"Backend libraries injected from this CMake build: {', '.join(added) if added else 'none; AAR already contained them'}")
    print(f"Vulkan-Headers commit: {args.vulkan_headers_commit}")
    print(f"Vulkan-Hpp include: {vulkan_include_dir / 'vulkan' / 'vulkan.hpp'}")
    print(f"SPIR-V include: {spirv_headers_include_dir / 'spirv' / 'unified1' / 'spirv.hpp'}")
    print(f"Runtime bundle: {runtime_zip}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(error.stdout or str(error), file=sys.stderr)
        raise
