# Technical notes

- Goal: build the upstream `llama-android` AAR with the same Kotlin API as the CPU package, but with llama.cpp Vulkan backend enabled.
- Native backend source is the upstream pinned `llama.cpp` git submodule, not an unrelated custom JNI runtime.
- Main patch: locate `GGML_VULKAN` by CMake option name (replace OFF, preserve ON, or insert if absent); add Android NDK Vulkan include and loader stub, host `glslc`, SPIRV-Headers CMake package; `GGML_BACKEND_DL=ON`; `BUILD_SHARED_LIBS=OFF` to build separate dynamically loadable Vulkan and CPU backend plugins.
- Outputs: AAR, extracted ARM64 native bundle, build manifest with upstream/submodule commits, CMake cache evidence, ELF dynamic dependencies, and SHA-256.
- The workflow fails if GGML_VULKAN is not ON, the CMake graph does not contain ggml-vulkan, JNI library is missing, Vulkan system loader is not a DT_NEEDED dependency, or an app-bundled `libvulkan.so` is found.
- No GGUF model, APK signing key, or Tap Translate base APK is included.


- 2026-11-09 fix: patcher no longer matches the whole comment-bearing source line. It handles whitespace/comment changes, absent/upstream-already-ON variants, and is idempotent. Offline tests run in CI before cloning upstream.


- 2026-11-09 fix v3: package verifier scans the full upstream checkout for CMakeCache and Ninja graph locations.
- 2026-11-09 fix v4: switch from static backend linkage to `GGML_BACKEND_DL=ON`. The AAR must contain `libggml-vulkan.so` and `libggml-cpu*.so`; `libggml-vulkan.so` itself must carry `DT_NEEDED libvulkan.so`. This avoids incorrectly looking for a Vulkan dependency on `libllama_jni.so` when the backend is a separate dynamically loaded plugin.
