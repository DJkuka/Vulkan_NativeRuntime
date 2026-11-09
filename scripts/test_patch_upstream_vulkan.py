#!/usr/bin/env python3
"""Offline regression tests for the Android Vulkan CMake argument patcher."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import tempfile

SCRIPT = Path(__file__).with_name("patch_upstream_vulkan.py")
spec = importlib.util.spec_from_file_location("patch_upstream_vulkan", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def run_case(source: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "library" / "build.gradle.kts"
        target.parent.mkdir(parents=True)
        target.write_text(source, encoding="utf-8")
        assert module.main_for_test(root) == 0
        patched = target.read_text(encoding="utf-8")
        before = patched
        assert module.main_for_test(root) == 0
        assert target.read_text(encoding="utf-8") == before
        return patched


UPSTREAM_STYLE = '''android {\n  defaultConfig {\n    externalNativeBuild {\n      cmake {\n        arguments += listOf(\n          "-DANDROID_STL=c++_shared",\n          "-DGGML_OPENMP=OFF", // CPU\n          "-DGGML_VULKAN=OFF", // comment may change\n          "-DGGML_BACKEND_DL=ON",\n          "-DBUILD_SHARED_LIBS=OFF",\n          "-DLLAMA_CURL=OFF",\n        )\n      }\n    }\n  }\n}\n'''

NO_VULKAN_STYLE = '''android {\n  defaultConfig {\n    externalNativeBuild {\n      cmake {\n        arguments += listOf(\n          "-DANDROID_STL=c++_shared",\n          "-DGGML_OPENMP=OFF",\n        )\n      }\n    }\n  }\n}\n'''


def main() -> None:
    a = run_case(UPSTREAM_STYLE)
    for expected in ('"-DGGML_VULKAN=ON"', '"-DGGML_BACKEND_DL=OFF"', '"-DBUILD_SHARED_LIBS=ON"'):
        assert expected in a, expected
    for wrong in ('"-DGGML_VULKAN=OFF"', '"-DGGML_BACKEND_DL=ON"', '"-DBUILD_SHARED_LIBS=OFF"'):
        assert wrong not in a, wrong
    assert '"-DVulkan_INCLUDE_DIR=${System.getenv("VULKAN_INCLUDE_DIR") ?: ""}"' in a
    b = run_case(NO_VULKAN_STYLE)
    for expected in ('"-DGGML_VULKAN=ON"', '"-DGGML_BACKEND_DL=OFF"', '"-DBUILD_SHARED_LIBS=ON"'):
        assert expected in b, expected
    print("PASS: conflicting OFF/ON values normalized to required shared-link configuration")
    print("PASS: missing Vulkan options inserted into the CMake arguments list")
    print("PASS: patch is idempotent")


if __name__ == "__main__":
    main()
