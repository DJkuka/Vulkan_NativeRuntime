#!/usr/bin/env python3
"""Offline regression tests for patch_upstream_vulkan.py."""
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
        result = module.main_for_test(root)
        assert result == 0, f"patcher failed with exit {result}"
        patched = target.read_text(encoding="utf-8")
        # Idempotence: a second pass must preserve file bytes.
        before = patched
        result = module.main_for_test(root)
        assert result == 0
        assert target.read_text(encoding="utf-8") == before
        return patched


UPSTREAM_STYLE = '''android {\n  defaultConfig {\n    externalNativeBuild {\n      cmake {\n        arguments += listOf(\n          "-DANDROID_STL=c++_shared",\n          "-DGGML_OPENMP=OFF", // CPU\n          "-DGGML_VULKAN=OFF", // comment may change\n          "-DLLAMA_CURL=OFF",\n        )\n      }\n    }\n  }\n}\n'''

NO_VULKAN_STYLE = '''android {\n  defaultConfig {\n    externalNativeBuild {\n      cmake {\n        arguments += listOf(\n          "-DANDROID_STL=c++_shared",\n          "-DGGML_OPENMP=OFF",\n        )\n      }\n    }\n  }\n}\n'''


def main() -> None:
    a = run_case(UPSTREAM_STYLE)
    assert '"-DGGML_VULKAN=ON"' in a
    assert '"-DGGML_VULKAN=OFF"' not in a
    assert '"-DGGML_BACKEND_DL=OFF"' in a
    assert '"-DBUILD_SHARED_LIBS=OFF"' in a
    assert '"-DVulkan_INCLUDE_DIR=${System.getenv("VULKAN_INCLUDE_DIR") ?: ""}"' in a
    b = run_case(NO_VULKAN_STYLE)
    assert '"-DGGML_VULKAN=ON"' in b
    print("PASS: OFF value replaced despite comment variation")
    print("PASS: missing Vulkan option inserted into CMake listOf")
    print("PASS: repeated patch runs are idempotent")


if __name__ == "__main__":
    main()
