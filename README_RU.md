# Hy-MT2 Vulkan native runtime для Android ARM64

## Назначение

Это отдельный проект сборки **native runtime**, совместимого по Kotlin API с используемой интеграцией `dev.ffmpegkit-maintained:llama-android:0.1.1`. Он не является диагностическим APK и не содержит модель GGUF.

Цель — получить AAR и отдельный набор ARM64 `.so`, в котором llama.cpp действительно собран с `GGML_VULKAN=ON`, а CPU backend остаётся включённым.

## Исправление ошибки `vulkan/vulkan.hpp` (v7.1)

Предыдущая сборка дошла до `ggml-vulkan.cpp`, но остановилась с `fatal error: 'vulkan/vulkan.hpp' file not found`. Это ожидаемое различие: NDK r27c содержит C Vulkan header `vulkan/vulkan.h`, но не поставляет C++ binding Vulkan-Hpp. В v7 workflow отдельно загружает KhronosGroup/Vulkan-Headers tag `v1.3.290`, закреплённый commit `b379292b2ab6df5771ba9870d53cf8b2c9295daf`, и передаёт его `include/` в CMake через `Vulkan_INCLUDE_DIR`. Родительский JNI CMake также явно добавляет этот каталог через `include_directories(BEFORE SYSTEM ...)`, чтобы путь гарантированно получил `ggml-vulkan.cpp`. Android NDK по-прежнему используется для `libvulkan.so` loader stub. До основной сборки выполняется отдельный CMake smoke-target с тем же Android NDK toolchain-файлом, ABI `arm64-v8a` и API 24. Он компилирует оба заголовка, но больше не вызывает `clang++` вручную: прежний ручной вызов неполно воспроизводил параметры NDK и попадал в host glibc (`/usr/include/features.h`). Packaging validator дополнительно требует, чтобы реальный CMakeCache ссылался на те же Vulkan-Headers и SPIRV-Headers include paths.

## Что было не так в предыдущих версиях

Upstream задаёт `-DGGML_VULKAN=OFF` в `library/build.gradle.kts`. Одной правки Gradle-аргумента оказалось недостаточно: в реальном CMake cache пользователя продолжало оставаться `GGML_VULKAN:BOOL=OFF`.

Теперь патчер изменяет **три уровня конфигурации**:

1. В `library/build.gradle.kts` выставляет `GGML_VULKAN=ON`, `GGML_CPU=ON`, `GGML_BACKEND_DL=OFF` и `BUILD_SHARED_LIBS=ON`.
2. В `library/src/main/jni/CMakeLists.txt` вставляет блок `set(... CACHE ... FORCE)` непосредственно перед `add_subdirectory(... llama.cpp)`.
3. В самом `llama.cpp/ggml` патчер находит реальный `option(GGML_VULKAN ... OFF)`, меняет default на `ON` и вставляет `set(GGML_VULKAN ON CACHE BOOL ... FORCE)` перед этой декларацией. Также изменяются возможные явные `set(GGML_VULKAN OFF ...)` в CMake-файлах submodule.
4. В родительском JNI CMake явно добавляются оба include-root через `include_directories(BEFORE SYSTEM ...)`: `$ENV{VULKAN_INCLUDE_DIR}` для `vulkan/vulkan.hpp` и `$ENV{SPIRV_HEADERS_INCLUDE_DIR}` для `spirv/unified1/spirv.hpp`. Это нужно даже при успешном `find_package(SPIRV-Headers CONFIG REQUIRED)`: обнаружение package config само по себе не гарантирует, что include-каталог попадёт в compile flags цели `ggml-vulkan`.

Третий уровень нужен из-за последнего лога: даже после исправления Gradle и родительского JNI CMake в реальном `.cxx/.../CMakeCache.txt` оставалось `GGML_VULKAN=OFF`. Теперь значение принудительно выставляется непосредственно в том CMake-файле, который объявляет опцию ggml.

Workflow удаляет `library/.cxx` и `library/build` перед сборкой. После конфигурации фактический `CMakeCache.txt` становится обязательным источником истины. Если там `GGML_VULKAN=OFF`, workflow немедленно останавливается. Проверки не ослаблены.

## Как устроено связывание backend

Используется комбинация:

```cmake
-DGGML_VULKAN=ON
-DGGML_CPU=ON
-DGGML_BACKEND_DL=OFF
-DBUILD_SHARED_LIBS=ON
```

Важное уточнение: `GGML_BACKEND_DL=OFF` **не означает**, что Vulkan backend статически встраивается внутрь `libggml.so`. В ggml при `BUILD_SHARED_LIBS=ON` backend targets создаются как обычные shared libraries и связываются с `libggml.so` через стандартные `DT_NEEDED` зависимости. При этом не используется режим `MODULE`/`dlopen` backend plugins.

Ожидаемая зависимость:

```text
libllama_jni.so
  └─ libllama.so
       └─ libggml.so
            ├─ libggml-vulkan.so
            │    └─ libvulkan.so  (системная библиотека Android)
            └─ libggml-cpu*.so   (CPU fallback)
```

AAR может не включить часть транзитивных native dependencies автоматически. Поэтому packaging-скрипт проверяет AAR и при необходимости добавляет `libggml-vulkan.so` и `libggml-cpu*.so`, **только если они найдены среди ARM64 результатов того же CMake build**. Скрипт не подменяет отсутствующие библиотеки host/Linux-файлами.

Системная `libvulkan.so` не копируется в AAR.

## Сборка через GitHub Actions

1. Создай отдельный GitHub-репозиторий для этого теста.
2. Загрузи в него содержимое архива, сохранив `.github/workflows`, `scripts`, `README_RU.md`.
3. Сделай commit в `main`.
4. Открой **Actions → Build llama-android Hy-MT2 Vulkan native runtime → Run workflow**.
5. После успешной сборки скачай:
   - `HyMT2-Vulkan-AAR-ARM64` — AAR;
   - `HyMT2-Vulkan-NativeRuntime-ARM64` — самостоятельный ZIP с AAR и native `.so`;
   - `HyMT2-Vulkan-Build-Diagnostics` — лог и `BUILD_MANIFEST.json`.

Workflow:

- устанавливает JDK 17, Android SDK, NDK r27c и CMake 3.22.1;
- устанавливает host `glslc`; SPIRV-Headers отдельно клонируется по закреплённому Khronos SDK tag и устанавливается в `$RUNNER_TEMP/spirv-headers-install`, а не в `/usr/include`;
- загружает официальный Khronos Vulkan-Headers `v1.3.290` (закреплённый commit), потому что Android NDK r27c содержит `vulkan/vulkan.h`, но не содержит C++-обёртку `vulkan/vulkan.hpp`, которую включает `ggml-vulkan.cpp`;
- отдельно устанавливает SPIRV-Headers из тега `vulkan-sdk-1.3.290.0`, commit `2acb319af38d43be3ea76bfabf3998e5281d8d12`: это другой репозиторий Khronos, он предоставляет `spirv/unified1/spirv.hpp`. Vulkan-Headers не содержит этот файл;
- не использует Ubuntu-копию SPIRV-Headers из `/usr/include`: CMake может считать `/usr/include` implicit host include path и не добавлять его в параметры Android cross-compile. Вместо этого заголовки устанавливаются в отдельный prefix под `$RUNNER_TEMP`;
- явно передаёт оба include-root в родительский JNI CMake через `include_directories(BEFORE SYSTEM ...)`. Это обходит обнаруженную проблему upstream: `find_package(SPIRV-Headers)` находит CMake package, но его include path не попадает в compile flags цели `ggml-vulkan` в данном Android build;
- выполняет regression-тест конфигурации headers и отдельный CMake smoke-target с Android NDK toolchain, который компилирует `vulkan/vulkan.hpp` и `spirv/unified1/spirv.hpp` до полной сборки;
- клонирует `ffmpegkit-maintained/llama-android` вместе с закреплённым submodule `llama.cpp` и проверяет tag `b9878`;
- патчит Gradle CMake args, JNI `CMakeLists.txt` и фактическую декларацию `option(GGML_VULKAN ...)` в ggml;
- передаёт Khronos Vulkan-Headers `include/` как `Vulkan_INCLUDE_DIR`, SPIRV-Headers `include/` как `SPIRV_HEADERS_INCLUDE_DIR`, Android NDK loader stub как `Vulkan_LIBRARY`, а также host shader compiler и путь к SPIRV-Headers CMake package;
- удаляет старые `.cxx` cache и выполняет `:library:assembleRelease`;
- печатает фактический CMake cache, Ninja graph, найденные backend build outputs и ELF dependencies;
- при необходимости добавляет shared backend `.so` из этого же ARM64 build в AAR;
- проверяет итоговую ELF-зависимость и публикует артефакты.

## Обязательные критерии успешной сборки

Actions не должен опубликовать runtime как успешный, пока не выполнено всё перечисленное:

1. Regression-тест запускает реальный CMake с конфликтующим `-DGGML_VULKAN=OFF` и подтверждает, что после патча `CMakeCache.txt` содержит `GGML_VULKAN=ON`.
2. Реальный Android `CMakeCache.txt` содержит `GGML_VULKAN=ON`, `GGML_CPU=ON`, `GGML_BACKEND_DL=OFF`, `BUILD_SHARED_LIBS=ON`.
3. Сгенерированный Ninja graph содержит обе цели: `ggml-vulkan` и `ggml-cpu`.
4. В AAR есть `libggml-vulkan.so` и хотя бы одна `libggml-cpu*.so` для `arm64-v8a`. Если AGP не упаковал их, packaging step добавляет результаты этого же CMake build.
5. `readelf` подтверждает AArch64 ELF64 для всех `.so`.
6. `libggml.so` содержит `DT_NEEDED` для `libggml-vulkan.so` и CPU backend.
7. `libggml-vulkan.so` содержит `DT_NEEDED` для Android `libvulkan.so`.
8. Реальный CMakeCache указывает `Vulkan_INCLUDE_DIR` на каталог Khronos Vulkan-Headers, в котором присутствует `vulkan/vulkan.hpp`, а `SPIRV_HEADERS_INCLUDE_DIR` — на каталог с `spirv/unified1/spirv.hpp`; обе директории передаются в compile options `ggml-vulkan`.
9. До основной NDK-сборки успешен отдельный CMake smoke-target, сконфигурированный через `$ANDROID_NDK/build/cmake/android.toolchain.cmake`, с `ANDROID_ABI=arm64-v8a` и `ANDROID_PLATFORM=android-24`; он компилирует оба include (`vulkan/vulkan.hpp` и `spirv/unified1/spirv.hpp`) тем же способом выбора libc++/bionic, что и Android native build.
10. В AAR отсутствует собственная копия системной `libvulkan.so`.
11. Kotlin API `dev.ffmpegkit.llama.Llama` и `LlamaConfig` присутствует в `classes.jar`.

Наличие `GGML_VULKAN=ON` только в Gradle или в тексте лога не считается достаточным доказательством.

## Подключение к Hy-MT2 для теста

В тестовом Gradle-проекте вместо Maven-зависимости:

```gradle
implementation("dev.ffmpegkit-maintained:llama-android:0.1.1")
```

положи `llama-android-vulkan.aar` в `app/libs/` и временно укажи:

```gradle
implementation(files("libs/llama-android-vulkan.aar"))
```

Остальной Kotlin API должен остаться прежним. Для проверки на планшете:

```kotlin
// CPU-only baseline
val cpuConfig = LlamaConfig(contextSize = 2048, threads = 4, gpuLayers = 0)

// Начальный Vulkan probe: offload только 2 слоя
val vulkanConfig = LlamaConfig(contextSize = 2048, threads = 4, gpuLayers = 2)
```

Сравни один и тот же короткий перевод на CPU и Vulkan. Затем попробуй 4 и 8 слоёв, каждый раз выгружая модель перед повторной загрузкой. Наличие Vulkan backend в AAR ещё не гарантирует, что все kernels и выделение памяти будут работать стабильно на конкретном драйвере Adreno 610.

## Ограничения и статус

- Проект готовит исходники и CI-сборку; GitHub Actions в этом окружении не запускался.
- Реальный AAR и его `.so` появятся только после успешного workflow.
- Проверка на Lenovo TB128FU / Adreno 610 возможна только на самом планшете.
- `libvulkan.so` предоставляется Android; не включай Linux loader из runner в APK/AAR.
- Vulkan C/C++ headers используются только на этапе компиляции. `vulkan.hpp` не упаковывается в APK/AAR.
- Это отдельный native-runtime этап. После успешной проверки можно будет заменить CPU AAR в основном Hy-MT2 проекте, не затрагивая OCR/Tesseract/AutoFill/Google Offline Translate.

## Источники

- Upstream `llama-android`: https://github.com/ffmpegkit-maintained/llama-android
- Upstream Gradle build file: https://github.com/ffmpegkit-maintained/llama-android/blob/main/library/build.gradle.kts
- llama.cpp ggml backend linking: https://github.com/ggml-org/llama.cpp/blob/master/ggml/src/CMakeLists.txt
- llama.cpp build options: https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md


## v8 — явная передача SPIRV-Headers include path

- Причина ошибки v7: `ggml-vulkan.cpp` включает `spirv/unified1/spirv.hpp`, который поставляется не в Vulkan-Headers, а в отдельном Khronos-проекте SPIRV-Headers.
- Workflow по-прежнему устанавливает пакет SPIRV-Headers и его CMake package config, но теперь отдельно находит фактический header root с `spirv/unified1/spirv.hpp`.
- JNI CMake сохраняет `SPIRV_HEADERS_INCLUDE_DIR` в CMakeCache и явно добавляет его через `include_directories(BEFORE SYSTEM ...)` до `add_subdirectory(llama.cpp)`. Это обходит случай, когда `find_package(SPIRV-Headers)` находит config, но include directory не попадает в compile flags ggml-vulkan.
- Android ARM64 smoke test теперь одновременно компилирует Vulkan-Hpp и SPIR-V C++ header.
- Packaging validator требует точный `SPIRV_HEADERS_INCLUDE_DIR` в реальном CMakeCache и наличие `spirv/unified1/spirv.hpp`.


## v8 — явная передача SPIRV-Headers include path

- Причина ошибки v7: `ggml-vulkan.cpp` включает `spirv/unified1/spirv.hpp`, который поставляется отдельным Khronos-проектом SPIRV-Headers, а не Vulkan-Headers.
- Workflow находит фактический каталог header по пакетному списку `spirv-headers`; если apt package или CMake config неполны, собирает и устанавливает официальный SPIRV-Headers из исходников.
- JNI CMake сохраняет `SPIRV_HEADERS_INCLUDE_DIR` в CMakeCache и явно добавляет его через `include_directories(BEFORE SYSTEM ...)` до `add_subdirectory(llama.cpp)`. Это обходит случай, когда `find_package(SPIRV-Headers)` находит config, но include path не попадает в compile flags `ggml-vulkan`.
- ARM64 smoke test теперь проверяет одновременно `vulkan/vulkan.hpp` и `spirv/unified1/spirv.hpp`.
- Packaging validator требует точный SPIRV include root в Android CMakeCache и проверяет физическое наличие заголовка.


## v9 — smoke test через Android NDK CMake toolchain

В v8 отдельная проверка заголовков вызывала `clang++` вручную с `--target=aarch64-none-linux-android24` и `--sysroot`. Лог показал, что libc++ из Android sysroot затем подтянул host `/usr/include/features.h`, который требует glibc-файл `bits/wordsize.h`. Это ошибка изолированной проверки toolchain, а не свидетельство несовместимости Vulkan с Android.

В v9 ручной вызов clang++ удалён. Вместо него добавлен минимальный CMake smoke-проект `scripts/android_vulkan_header_smoke`, который конфигурируется через официальный `$ANDROID_NDK/build/cmake/android.toolchain.cmake` с `ANDROID_ABI=arm64-v8a`, `ANDROID_PLATFORM=android-24`, `ANDROID_STL=c++_shared`. Внутри target компилирует `vulkan/vulkan.hpp` и `spirv/unified1/spirv.hpp`. Это использует Android toolchain setup вместо смешения Android libc++ и host glibc include paths.

Основная Gradle/NDK-сборка по-прежнему остаётся окончательной проверкой совместимости `ggml-vulkan.cpp`; smoke test лишь проверяет заголовки тем же способом настройки компилятора.


## v10 — SPIRV-Headers staged include root (исправление v9 smoke test)

Лог v9 показал, что smoke-test компилировался с `-isystem .../Vulkan-Headers/include`, но без SPIRV-Headers. Причина: предыдущий workflow предпочитал пакет Ubuntu `spirv-headers`, который размещает include root в `/usr/include`. Для cross-compiler CMake может считать `/usr/include` implicit host include directory и не добавлять его в командную строку Android clang. Это объясняет, почему smoke-test видел `vulkan.hpp`, но не видел `spirv.hpp`.

В v10 SPIRV-Headers больше не берётся из системного apt-пакета. Workflow клонирует Khronos tag `vulkan-sdk-1.3.290.0`, проверяет commit `2acb319af38d43be3ea76bfabf3998e5281d8d12`, устанавливает проект в `$RUNNER_TEMP/spirv-headers-install` и задаёт include root `$RUNNER_TEMP/spirv-headers-install/include`. Это нестандартный staged path, который должен явно присутствовать в compile command.

Дополнительно добавлен `verify_android_smoke_compile_commands.py`: после CMake configure он читает `compile_commands.json` и требует наличие обоих точных include roots в команде компиляции `android_vulkan_header_smoke.cpp`. Проверка отдельно отклоняет `/usr/include`. Такой же staged `SPIRV_HEADERS_INCLUDE_DIR` передаётся в JNI CMake и используется реальной целью `ggml-vulkan`.


## v11 — исправление упаковщика после ошибки AttributeError

В v10 аргумент `--vulkan-include-dir` был объявлен без `type=Path`, хотя ниже использовался как объект `Path` (`.resolve()`). Это вызывало `AttributeError: 'str' object has no attribute 'resolve'` до выполнения проверок AAR. В v11 CLI вынесен в `build_parser()`, а `--vulkan-include-dir` получает `type=Path`. Добавлен regression-тест разбора аргументов: и `--vulkan-include-dir`, и `--spirv-headers-include-dir` должны быть объектами `pathlib.Path`. Критерии Vulkan-проверки не ослаблялись.
