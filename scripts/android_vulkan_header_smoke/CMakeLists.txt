cmake_minimum_required(VERSION 3.22.1)
project(android_vulkan_header_smoke LANGUAGES CXX)

if(NOT ANDROID)
    message(FATAL_ERROR "This smoke test must be configured with the Android NDK toolchain")
endif()
if(NOT CMAKE_ANDROID_ARCH_ABI STREQUAL "arm64-v8a")
    message(FATAL_ERROR "Expected arm64-v8a, got: ${CMAKE_ANDROID_ARCH_ABI}")
endif()
if(NOT DEFINED VULKAN_INCLUDE_DIR OR NOT EXISTS "${VULKAN_INCLUDE_DIR}/vulkan/vulkan.hpp")
    message(FATAL_ERROR "VULKAN_INCLUDE_DIR must contain vulkan/vulkan.hpp")
endif()
if(NOT DEFINED SPIRV_HEADERS_INCLUDE_DIR OR NOT EXISTS "${SPIRV_HEADERS_INCLUDE_DIR}/spirv/unified1/spirv.hpp")
    message(FATAL_ERROR "SPIRV_HEADERS_INCLUDE_DIR must contain spirv/unified1/spirv.hpp")
endif()
if(SPIRV_HEADERS_INCLUDE_DIR STREQUAL "/usr/include" OR SPIRV_HEADERS_INCLUDE_DIR MATCHES "^/usr/include/")
    message(FATAL_ERROR "SPIRV_HEADERS_INCLUDE_DIR must be staged outside host /usr/include for Android cross-compilation")
endif()
message(STATUS "Android Vulkan-Hpp include root: ${VULKAN_INCLUDE_DIR}")
message(STATUS "Android SPIRV-Headers include root: ${SPIRV_HEADERS_INCLUDE_DIR}")

add_library(android_vulkan_header_smoke OBJECT android_vulkan_header_smoke.cpp)
target_compile_features(android_vulkan_header_smoke PRIVATE cxx_std_17)
target_include_directories(android_vulkan_header_smoke SYSTEM BEFORE PRIVATE
    "${VULKAN_INCLUDE_DIR}"
    "${SPIRV_HEADERS_INCLUDE_DIR}"
)
