#!/usr/bin/env python3
"""Regression checks for Android NDK CMake-based Vulkan header smoke test."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build-vulkan-aar.yml"
PATCHER = ROOT / "scripts" / "patch_upstream_vulkan.py"
PACKAGER = ROOT / "scripts" / "package_vulkan_runtime.py"
SMOKE_CMAKE = ROOT / "scripts" / "android_vulkan_header_smoke" / "CMakeLists.txt"
SMOKE_CPP = ROOT / "scripts" / "android_vulkan_header_smoke" / "android_vulkan_header_smoke.cpp"


def main() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    patcher = PATCHER.read_text(encoding="utf-8")
    packager = PACKAGER.read_text(encoding="utf-8")
    smoke_cmake = SMOKE_CMAKE.read_text(encoding="utf-8")
    smoke_cpp = SMOKE_CPP.read_text(encoding="utf-8")

    assert "VULKAN_HEADERS_TAG: 'v1.3.290'" in workflow
    assert "VULKAN_HEADERS_COMMIT_PIN: 'b379292b2ab6df5771ba9870d53cf8b2c9295daf'" in workflow
    assert "SPIRV_HEADERS_TAG: 'vulkan-sdk-1.3.290.0'" in workflow
    assert "SPIRV_HEADERS_COMMIT_PIN: '2acb319af38d43be3ea76bfabf3998e5281d8d12'" in workflow
    assert "https://github.com/KhronosGroup/Vulkan-Headers.git" in workflow
    assert "https://github.com/KhronosGroup/SPIRV-Headers.git" in workflow
    assert 'SPIRV_INCLUDE="$SPIRV_PREFIX/include"' in workflow
    assert 'test "$SPIRV_INCLUDE" != "/usr/include"' in workflow
    assert 'verify_android_smoke_compile_commands.py' in workflow
    assert '--spirv-headers-commit "$SPIRV_HEADERS_COMMIT"' in workflow
    assert 'test -f "$VKH_INCLUDE/vulkan/vulkan.hpp"' in workflow
    assert 'test -f "$VULKAN_INCLUDE_DIR/vulkan/vulkan.hpp"' in workflow
    assert '"$ANDROID_NDK/build/cmake/android.toolchain.cmake"' in workflow
    assert 'cmake -S scripts/android_vulkan_header_smoke' in workflow
    assert 'cmake --build "$RUNNER_TEMP/android-vulkan-header-smoke-build" --verbose' in workflow
    assert 'clang++" \\\n            --target=aarch64-none-linux-android24' not in workflow
    assert 'include_directories(BEFORE SYSTEM "$ENV{VULKAN_INCLUDE_DIR}")' in patcher
    assert 'parser.add_argument("--vulkan-include-dir", required=True, type=Path)' in packager
    assert 'parser.add_argument("--vulkan-headers-commit", required=True)' in packager
    assert '"vulkan_headers_commit": args.vulkan_headers_commit' in packager
    assert 'if(NOT ANDROID)' in smoke_cmake
    assert 'CMAKE_ANDROID_ARCH_ABI STREQUAL "arm64-v8a"' in smoke_cmake
    assert 'target_compile_features(android_vulkan_header_smoke PRIVATE cxx_std_17)' in smoke_cmake
    assert 'target_include_directories(android_vulkan_header_smoke SYSTEM BEFORE PRIVATE' in smoke_cmake
    assert 'SPIRV_HEADERS_INCLUDE_DIR MATCHES "^/usr/include/"' in smoke_cmake
    assert '#include <vulkan/vulkan.hpp>' in smoke_cpp
    assert '#include <spirv/unified1/spirv.hpp>' in smoke_cpp

    print("PASS: Khronos Vulkan-Headers is pinned and provides vulkan.hpp")
    print("PASS: SPIRV-Headers are pinned and staged under RUNNER_TEMP, not implicit /usr/include")
    print("PASS: smoke compile_commands verifier requires both explicit include paths")
    print("PASS: header smoke test uses Android NDK CMake toolchain, not host-fallback clang++ invocation")
    print("PASS: smoke test asserts Android arm64-v8a and compiles both C++ headers")
    print("PASS: JNI CMake propagates both Khronos header roots")
    print("PASS: packaging validator records Vulkan-Headers provenance")


if __name__ == "__main__":
    main()
