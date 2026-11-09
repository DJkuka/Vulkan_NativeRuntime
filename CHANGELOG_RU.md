
## v6 — GGML option-level Vulkan FORCE block

- Исправлена главная причина: Gradle patch alone did not prevent the generated Android CMake cache from retaining `GGML_VULKAN=OFF`.
- `library/src/main/jni/CMakeLists.txt` теперь получает `set(GGML_VULKAN ON CACHE BOOL ... FORCE)` и CPU/backend options immediately before llama.cpp is added.
- Workflow clears stale `library/.cxx` and `library/build` before configuring.
- Clarified CMake semantics: `GGML_BACKEND_DL=OFF` disables MODULE/dlopen plugin mode, but with `BUILD_SHARED_LIBS=ON`, Vulkan and CPU backends are ordinary shared libraries linked through `DT_NEEDED`.
- Restored same-build AAR injection and validation for `libggml-vulkan.so` and `libggml-cpu*.so`.
- Added regression tests for effective CMake values, generated targets, and AAR injection.

- Added a third configuration layer: patch the actual `option(GGML_VULKAN ...)` declaration in the pinned `llama.cpp/ggml` CMake tree, change its default to ON, and force the cache ON at that declaration. Explicit GGML_VULKAN OFF assignments in CMake sources are normalized to ON.
- Added a real CMake regression configure with conflicting `-DGGML_VULKAN=OFF`; test requires generated `CMakeCache.txt` to contain `GGML_VULKAN:BOOL=ON`.
