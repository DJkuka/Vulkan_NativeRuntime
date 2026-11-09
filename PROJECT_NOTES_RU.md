# Technical notes

- Goal: build the upstream `llama-android` AAR with the same Kotlin API as the CPU package, but with llama.cpp Vulkan backend enabled.
- Native backend source is the upstream pinned `llama.cpp` git submodule, not an unrelated custom JNI runtime.
- Main patch: CMake option `GGML_VULKAN=OFF` -> `GGML_VULKAN=ON`; add Android NDK Vulkan include and loader stub, host `glslc`, SPIRV-Headers CMake package; `GGML_BACKEND_DL=OFF`; `BUILD_SHARED_LIBS=OFF` to keep backend statically linked into JNI runtime.
- Outputs: AAR, extracted ARM64 native bundle, build manifest with upstream/submodule commits, CMake cache evidence, ELF dynamic dependencies, and SHA-256.
- The workflow fails if GGML_VULKAN is not ON, the CMake graph does not contain ggml-vulkan, JNI library is missing, Vulkan system loader is not a DT_NEEDED dependency, or an app-bundled `libvulkan.so` is found.
- No GGUF model, APK signing key, or Tap Translate base APK is included.
