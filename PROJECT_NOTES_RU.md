# Технические заметки — Vulkan Native Runtime v6

## Корневая причина предыдущих отказов

В v5 реальный `library/.cxx/.../CMakeCache.txt` всё ещё показывал `GGML_VULKAN=OFF`, хотя скрипт изменял `build.gradle.kts`. Значит, правка только Gradle-аргумента не управляла конечным значением CMake cache в данной upstream-сборке.

В v6 patched authoritative JNI entry point и сам источник декларации опции внутри pinned llama.cpp:

- `library/src/main/jni/CMakeLists.txt`
- `llama.cpp/ggml/CMakeLists.txt` (файл определяется поиском точной `option(GGML_VULKAN ...)` декларации)

Родительский JNI блок устанавливает `GGML_VULKAN=ON` до `add_subdirectory(... llama.cpp)`. Дополнительно патчер вставляет `set(GGML_VULKAN ON CACHE BOOL ... FORCE)` непосредственно перед `option(GGML_VULKAN ...)` в ggml CMake и заменяет default `OFF` на `ON`. Все явные `set(GGML_VULKAN OFF ...)` CMake-назначения в submodule нормализуются до `ON`.

Последний пользовательский cache log показал, что после одного лишь Gradle/JNI patch эффективная настройка оставалась OFF. Поэтому новый регрессионный тест действительно запускает CMake с конфликтующим `-DGGML_VULKAN=OFF` и требует, чтобы созданный CMakeCache всё равно содержал `GGML_VULKAN:BOOL=ON`.

Workflow удаляет `.cxx` и `library/build` перед Gradle сборкой. Если итоговый cache всё равно покажет OFF, build прекращается с сообщением об эффективных значениях cache.

## Backend link mode: точное значение опций

- `GGML_VULKAN=ON`: собрать Vulkan backend.
- `GGML_CPU=ON`: оставить CPU backend.
- `GGML_BACKEND_DL=OFF`: backend targets не собираются как `MODULE` для `dlopen` plugin scan.
- `BUILD_SHARED_LIBS=ON`: backend targets всё равно являются обычными shared libraries. CMake `ggml_add_backend_library()` при `GGML_BACKEND_DL=OFF` добавляет backend через `add_library(${backend} ...)` и связывает его с `ggml` через `target_link_libraries(ggml PUBLIC ${backend})`.

То есть ожидаются `libggml-vulkan.so` и `libggml-cpu*.so`, связанные в нормальную DT_NEEDED цепочку, а не плагин для динамического поиска и не статический backend внутри `libggml.so`.

## CMake toolchain

Host tools: `glslc`, SPIR-V-Headers CMake package.

Target Android ARM64 values:
- Vulkan headers: `$ANDROID_NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/include`
- Vulkan loader stub: `$ANDROID_NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/lib/aarch64-linux-android/libvulkan.so`
- CMake variables: `Vulkan_INCLUDE_DIR`, `Vulkan_LIBRARY`, `Vulkan_GLSLC_EXECUTABLE`, `SPIRV-Headers_DIR`.

В `libggml-vulkan.so` должен быть `DT_NEEDED libvulkan.so`, но сам системный loader в AAR не включается.

## Порядок проверки

1. Проверить patcher Gradle + JNI CMake; включая исходное принудительное `GGML_VULKAN=OFF`, которое наш patch block должен переопределить позже.
2. Удалить старый AGP `.cxx` cache.
3. Собрать upstream с pinned llama.cpp `b9878`.
4. CMake cache: Vulkan=ON, CPU=ON, backend dl=OFF, shared libs=ON.
5. Ninja graph: `ggml-vulkan` и `ggml-cpu`.
6. AAR: `libggml-vulkan.so`, `libggml-cpu*.so`, `libggml.so`, `libllama.so`, `libllama_jni.so`.
7. Если shared backend отсутствует в AAR, найти только ARM64 output из того же CMake build и добавить.
8. ELF link checks: `libggml.so -> libggml-vulkan.so -> libvulkan.so`, плюс `libggml.so -> libggml-cpu*.so`.
9. Verify Kotlin API classes and publish diagnostics + runtime bundle.

## История версий

- v1: fragile Gradle string replacement.
- v2: patch options by name.
- v3: broader CMake cache/Ninja discovery.
- v4: attempted `GGML_BACKEND_DL=ON` with `BUILD_SHARED_LIBS=OFF`; invalid configuration for backend plugins.
- v5: `BUILD_SHARED_LIBS=ON`, `GGML_BACKEND_DL=OFF`, but still only Gradle patch; effective cache remained `GGML_VULKAN=OFF` in user's runner. It also incorrectly described regular shared backend dependencies as absent in non-dlopen mode.
- v6: force the effective value at three levels: Gradle args, JNI parent CMakeLists, and the actual `option(GGML_VULKAN ...)` declaration in the pinned ggml CMake source. Also normalize explicit OFF assignments and add a real CMake cache regression test. Ordinary shared backend dependency checks and same-build AAR injection remain strict.

## Степень проверки этой ревизии

- Python syntax check.
- Regression test ensures parent JNI CMake FORCE block is before `add_subdirectory(llama.cpp)`.
- Regression test patches the actual ggml `option(GGML_VULKAN ...)` declaration and runs a real CMake configure with `-DGGML_VULKAN=OFF`, requiring effective cache ON.
- Idempotence test for repeated patcher execution.
- CMakeCache parsing tests distinguishing ON from OFF.
- Ninja graph checks for Vulkan and CPU backend target identifiers.
- Packaging tests verify same-build AArch64 backend objects are added to AAR without duplicate entries.
- Full Android NDK/Gradle build and device test remain to be run in GitHub Actions and on the tablet.


## v7 fix: missing Vulkan-Hpp header

The v6 build reached `ggml-vulkan.cpp` and failed because it included `vulkan/vulkan.hpp`. Android NDK r27c provides the C header `vulkan/vulkan.h`, not the C++ Vulkan-Hpp wrapper. The previous workflow pointed `Vulkan_INCLUDE_DIR` at the NDK sysroot, so the Hpp include could not resolve. v7 fetches KhronosGroup/Vulkan-Headers tag `v1.3.290`, uses its `include/` for compile-time headers, retains the NDK ARM64 `libvulkan.so` stub for linking, performs an Android-targeted Hpp smoke compile, and validates the same include path in the generated CMakeCache.


## v7.1: propagate Vulkan-Hpp include path

The compile log proves that `ggml-vulkan.cpp` cannot see `vulkan/vulkan.hpp`; the NDK sysroot provides only the C headers. The workflow now pins Vulkan-Headers tag `v1.3.290` to commit `b379292b2ab6df5771ba9870d53cf8b2c9295daf`. The JNI CMake root sets `Vulkan_INCLUDE_DIR` and explicitly applies `include_directories(BEFORE SYSTEM ...)` before adding llama.cpp. The workflow also performs an Android ARM64 C++ syntax-only smoke compile of the actual header and the package validator requires the exact header path in the generated Android CMakeCache.


## v9 correction: Android CMake smoke test

The v8 standalone smoke test invoked NDK `clang++` manually with a target/sysroot. On the GitHub runner, libc++ then included `/usr/include/features.h`, i.e. host glibc, which failed on missing `bits/wordsize.h`. This is a false negative caused by the standalone compiler invocation not reproducing AGP's Android toolchain configuration.

The v9 workflow removes the manual clang++ call and configures a minimal CMake object target using `$ANDROID_NDK/build/cmake/android.toolchain.cmake`, `ANDROID_ABI=arm64-v8a`, `ANDROID_PLATFORM=android-24`, and `ANDROID_STL=c++_shared`. The target compiles both Vulkan-Hpp and SPIR-V headers. The real Gradle externalNativeBuild remains authoritative for the final ggml-vulkan compile.
