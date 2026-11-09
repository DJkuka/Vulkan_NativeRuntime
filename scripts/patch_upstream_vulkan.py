#!/usr/bin/env python3
"""Patch only the upstream llama-android native CMake arguments for Vulkan."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

OLD = '"-DGGML_VULKAN=OFF", // Free = CPU/NEON only (Pro enables Vulkan)'
NEW = '''"-DGGML_VULKAN=ON", // Test build: compile the Vulkan backend
                "-DGGML_BACKEND_DL=OFF", // Statically link backend registration into llama_jni
                "-DBUILD_SHARED_LIBS=OFF", // Keep ggml/llama/backend code in the JNI runtime
                "-DVulkan_INCLUDE_DIR=${System.getenv("VULKAN_INCLUDE_DIR") ?: ""}",
                "-DVulkan_LIBRARY=${System.getenv("VULKAN_LIBRARY") ?: ""}",
                "-DVulkan_GLSLC_EXECUTABLE=${System.getenv("VULKAN_GLSLC_EXECUTABLE") ?: ""}",
                "-DSPIRV-Headers_DIR=${System.getenv("SPIRV_HEADERS_DIR") ?: ""}",'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("upstream", type=Path, help="Root of cloned llama-android repository")
    args = ap.parse_args()
    path = args.upstream / "library" / "build.gradle.kts"
    if not path.is_file():
        print(f"ERROR: upstream Gradle file not found: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    count = text.count(OLD)
    if count != 1:
        print(f"ERROR: expected exactly one original GGML_VULKAN=OFF line, found {count}", file=sys.stderr)
        return 3
    patched = text.replace(OLD, NEW)
    if '"-DGGML_VULKAN=OFF"' in patched or '"-DGGML_VULKAN=ON"' not in patched:
        print("ERROR: failed to replace upstream Vulkan option safely", file=sys.stderr)
        return 4
    path.write_text(patched, encoding="utf-8")
    print("Patched upstream library/build.gradle.kts:")
    print("  GGML_VULKAN=ON")
    print("  GGML_BACKEND_DL=OFF")
    print("  BUILD_SHARED_LIBS=OFF")
    print("  Android NDK Vulkan headers + loader stub + host glslc/SPIRV-Headers passed to CMake")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
