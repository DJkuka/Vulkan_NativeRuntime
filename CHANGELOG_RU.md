
## v6 — GGML option-level Vulkan FORCE block

- Исправлена главная причина: Gradle patch alone did not prevent the generated Android CMake cache from retaining `GGML_VULKAN=OFF`.
- `library/src/main/jni/CMakeLists.txt` теперь получает `set(GGML_VULKAN ON CACHE BOOL ... FORCE)` и CPU/backend options immediately before llama.cpp is added.
- Workflow clears stale `library/.cxx` and `library/build` before configuring.
- Clarified CMake semantics: `GGML_BACKEND_DL=OFF` disables MODULE/dlopen plugin mode, but with `BUILD_SHARED_LIBS=ON`, Vulkan and CPU backends are ordinary shared libraries linked through `DT_NEEDED`.
- Restored same-build AAR injection and validation for `libggml-vulkan.so` and `libggml-cpu*.so`.
- Added regression tests for effective CMake values, generated targets, and AAR injection.

- Added a third configuration layer: patch the actual `option(GGML_VULKAN ...)` declaration in the pinned `llama.cpp/ggml` CMake tree, change its default to ON, and force the cache ON at that declaration. Explicit GGML_VULKAN OFF assignments in CMake sources are normalized to ON.
- Added a real CMake regression configure with conflicting `-DGGML_VULKAN=OFF`; test requires generated `CMakeCache.txt` to contain `GGML_VULKAN:BOOL=ON`.


## v7 — исправлена компиляция Vulkan-Hpp

- Исправлена ошибка `fatal error: vulkan/vulkan.hpp file not found`.
- Добавлена загрузка официальных Khronos Vulkan-Headers tag `v1.3.290`.
- `Vulkan_INCLUDE_DIR` теперь указывает на `Vulkan-Headers/include`, а `Vulkan_LIBRARY` остаётся Android NDK loader stub.
- Удалена зависимость от host `libvulkan-dev` как источника заголовков; host-пакет не должен подменять Android include/library paths.
- Добавлена предварительная NDK ARM64 smoke-компиляция `#include <vulkan/vulkan.hpp>`.
- Package validator требует точный `Vulkan_INCLUDE_DIR` из реального Android CMakeCache и сохраняет commit Vulkan-Headers в manifest.


## v7.1 — гарантирован Vulkan-Hpp include для ggml-vulkan

- Причина build failure: `ggml-vulkan.cpp` включает `vulkan/vulkan.hpp`, которого нет в Android NDK r27c.
- Vulkan-Headers tag `v1.3.290` теперь дополнительно закреплён точным commit `b379292b2ab6df5771ba9870d53cf8b2c9295daf`.
- JNI CMake применяет `include_directories(BEFORE SYSTEM ...)` для загруженного каталога `Vulkan-Headers/include`, явно передавая его всем подпроектам.
- Smoke test компилирует `vulkan.hpp` с Android ARM64 target; cache validator требует тот же include path.

- Добавлен отдельный offline regression-тест `scripts/test_vulkan_headers_workflow.py`, проверяющий pin, CMake include propagation, smoke-test wiring и manifest provenance.
- Package validator отклоняет отсутствующий/неполный SHA Vulkan-Headers.


## v9
- Убран ручной вызов NDK `clang++` для smoke-test: в нём libc++ цеплял host `/usr/include/features.h` и падал на glibc `bits/wordsize.h`.
- Добавлен минимальный CMake smoke-проект, собранный через официальный Android NDK toolchain file с `arm64-v8a`, API 24 и `c++_shared`.
- Smoke target по-прежнему компилирует оба обязательных заголовка: Vulkan-Hpp и SPIRV-Headers.
- Обновлены regression-тест и документация. Основная Gradle/NDK-сборка остаётся финальным доказательством, что `ggml-vulkan.cpp` компилируется.


## v10

- Исправлен v9 header smoke test: SPIRV-Headers больше не берётся из Ubuntu `/usr/include`, поскольку CMake может опускать implicit host include path из Android cross-compile flags.
- SPIRV-Headers берётся из закреплённого Khronos tag `vulkan-sdk-1.3.290.0` / commit `2acb319af38d43be3ea76bfabf3998e5281d8d12` и устанавливается в `$RUNNER_TEMP/spirv-headers-install`.
- Добавлен анализ `compile_commands.json`, который требует фактическое присутствие Vulkan-Headers и SPIRV-Headers путей в Android smoke compile command.
- Packaging manifest сохраняет commit SPIRV-Headers; validator отклоняет системный `/usr/include` как SPIRV include root.
