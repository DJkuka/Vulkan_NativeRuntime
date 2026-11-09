#!/usr/bin/env python3
"""Offline regression tests for Gradle args + authoritative JNI CMake patch."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import tempfile
import subprocess

SCRIPT = Path(__file__).with_name("patch_upstream_vulkan.py")
spec = importlib.util.spec_from_file_location("patch_upstream_vulkan", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def run_case(gradle_source: str, cmake_source: str, ggml_source: str) -> tuple[str, str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        gradle = root / "library" / "build.gradle.kts"
        cmake = root / "library" / "src" / "main" / "jni" / "CMakeLists.txt"
        ggml = root / "llama.cpp" / "ggml" / "CMakeLists.txt"
        gradle.parent.mkdir(parents=True)
        cmake.parent.mkdir(parents=True)
        ggml.parent.mkdir(parents=True)
        gradle.write_text(gradle_source, encoding="utf-8")
        cmake.write_text(cmake_source, encoding="utf-8")
        ggml.write_text(ggml_source, encoding="utf-8")
        assert module.main_for_test(root) == 0
        # Exercise CMake itself with a conflicting command-line OFF value.
        # The patched ggml CMake source must still produce an ON cache.
        build_dir = root / "cmake-configure-test"
        subprocess.run(
            ["cmake", "-S", str(ggml.parent), "-B", str(build_dir), "-DGGML_VULKAN=OFF"],
            check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        cache = (build_dir / "CMakeCache.txt").read_text(encoding="utf-8")
        assert "GGML_VULKAN:BOOL=ON" in cache, cache
        patched_gradle = gradle.read_text(encoding="utf-8")
        patched_cmake = cmake.read_text(encoding="utf-8")
        patched_ggml = ggml.read_text(encoding="utf-8")
        before = (patched_gradle, patched_cmake, patched_ggml)
        assert module.main_for_test(root) == 0
        assert (
            gradle.read_text(encoding="utf-8"),
            cmake.read_text(encoding="utf-8"),
            ggml.read_text(encoding="utf-8"),
        ) == before
        return before


UPSTREAM_GRADLE = '''android {\n  defaultConfig {\n    externalNativeBuild {\n      cmake {\n        arguments += listOf(\n          "-DANDROID_STL=c++_shared",\n          "-DGGML_OPENMP=OFF", // CPU\n          "-DGGML_VULKAN=OFF", // Pro enables Vulkan\n          "-DLLAMA_CURL=OFF",\n        )\n        targets += "llama_jni"\n      }\n    }\n  }\n}\n'''

UPSTREAM_CMAKE = '''cmake_minimum_required(VERSION 3.22.1)\nproject("llama_jni")\nset(LLAMA_CPP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../../../../llama.cpp")\n# A hypothetical upstream force-off must be superseded by our generated block.\nset(GGML_VULKAN OFF CACHE BOOL "disable" FORCE)\nadd_subdirectory(\n    ${LLAMA_CPP_DIR}\n    llama.cpp\n)\nadd_library(llama_jni SHARED llama_jni.cpp)\ntarget_link_libraries(llama_jni llama log android)\n'''

UPSTREAM_GGML = '''cmake_minimum_required(VERSION 3.22.1)\nproject(ggml NONE)\nset(GGML_VULKAN OFF CACHE BOOL "upstream forced off" FORCE)\noption(GGML_VULKAN "ggml: use Vulkan" OFF)\nmessage(STATUS "GGML_VULKAN effective value: ${GGML_VULKAN}")\n'''


def main() -> None:
    gradle, cmake, ggml = run_case(UPSTREAM_GRADLE, UPSTREAM_CMAKE, UPSTREAM_GGML)
    for expected in (
        '"-DGGML_VULKAN=ON"',
        '"-DGGML_BACKEND_DL=OFF"',
        '"-DBUILD_SHARED_LIBS=ON"',
        '"-DGGML_CPU=ON"',
    ):
        assert expected in gradle, expected
    for wrong in ('"-DGGML_VULKAN=OFF"', '"-DGGML_BACKEND_DL=ON"', '"-DBUILD_SHARED_LIBS=OFF"'):
        assert wrong not in gradle, wrong
    assert module.CMAKE_MARKER_BEGIN in cmake
    assert "set(GGML_VULKAN ON CACHE BOOL" in cmake
    assert "set(GGML_CPU ON CACHE BOOL" in cmake
    assert "set(GGML_BACKEND_DL OFF CACHE BOOL" in cmake
    assert cmake.index('set(GGML_VULKAN OFF CACHE BOOL "disable" FORCE)') < cmake.index(module.CMAKE_MARKER_BEGIN)
    assert cmake.index(module.CMAKE_MARKER_BEGIN) < cmake.index("add_subdirectory(")
    assert '"$ENV{VULKAN_LIBRARY}" CACHE FILEPATH' in cmake
    assert module.GGML_MARKER_BEGIN in ggml
    assert 'set(GGML_VULKAN ON CACHE BOOL "Enable ggml Vulkan backend for Android" FORCE)' in ggml
    assert ggml.index(module.GGML_MARKER_BEGIN) < ggml.index('option(GGML_VULKAN')
    assert 'option(GGML_VULKAN "ggml: use Vulkan" ON)' in ggml
    assert 'set(GGML_VULKAN OFF CACHE BOOL' not in ggml
    print("PASS: Gradle options normalized and present")
    print("PASS: JNI CMake FORCE block precedes llama.cpp subdirectory")
    print("PASS: ggml's actual option() declaration is forced ON and default OFF is removed")
    print("PASS: real CMake configure overrides a conflicting -DGGML_VULKAN=OFF and writes ON to CMakeCache")
    print("PASS: Vulkan paths forwarded from Android NDK/host toolchain")
    print("PASS: patch remains idempotent")


if __name__ == "__main__":
    main()
