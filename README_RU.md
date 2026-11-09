# Hy-MT2 Vulkan native runtime для Android ARM64

## Назначение

Это отдельный проект сборки **native runtime**, совместимого по Kotlin API с используемой интеграцией `dev.ffmpegkit-maintained:llama-android:0.1.1`. Он не является диагностическим APK и не содержит модель GGUF.

Цель — получить AAR и отдельный набор ARM64 `.so`, в котором llama.cpp действительно собран с `GGML_VULKAN=ON`, а CPU backend остаётся включённым.

## Что было не так в предыдущих версиях

Upstream задаёт `-DGGML_VULKAN=OFF` в `library/build.gradle.kts`. Одной правки Gradle-аргумента оказалось недостаточно: в реальном CMake cache пользователя продолжало оставаться `GGML_VULKAN:BOOL=OFF`.

Теперь патчер изменяет **три уровня конфигурации**:

1. В `library/build.gradle.kts` выставляет `GGML_VULKAN=ON`, `GGML_CPU=ON`, `GGML_BACKEND_DL=OFF` и `BUILD_SHARED_LIBS=ON`.
2. В `library/src/main/jni/CMakeLists.txt` вставляет блок `set(... CACHE ... FORCE)` непосредственно перед `add_subdirectory(... llama.cpp)`.
3. В самом `llama.cpp/ggml` патчер находит реальный `option(GGML_VULKAN ... OFF)`, меняет default на `ON` и вставляет `set(GGML_VULKAN ON CACHE BOOL ... FORCE)` перед этой декларацией. Также изменяются возможные явные `set(GGML_VULKAN OFF ...)` в CMake-файлах submodule.

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
- устанавливает host `glslc` и SPIR-V headers;
- клонирует `ffmpegkit-maintained/llama-android` вместе с закреплённым submodule `llama.cpp` и проверяет tag `b9878`;
- патчит Gradle CMake args, JNI `CMakeLists.txt` и фактическую декларацию `option(GGML_VULKAN ...)` в ggml;
- передаёт Android NDK Vulkan headers/loader stub и host shader compiler;
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
8. В AAR отсутствует собственная копия системной `libvulkan.so`.
9. Kotlin API `dev.ffmpegkit.llama.Llama` и `LlamaConfig` присутствует в `classes.jar`.

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
- Это отдельный native-runtime этап. После успешной проверки можно будет заменить CPU AAR в основном Hy-MT2 проекте, не затрагивая OCR/Tesseract/AutoFill/Google Offline Translate.

## Источники

- Upstream `llama-android`: https://github.com/ffmpegkit-maintained/llama-android
- Upstream Gradle build file: https://github.com/ffmpegkit-maintained/llama-android/blob/main/library/build.gradle.kts
- llama.cpp ggml backend linking: https://github.com/ggml-org/llama.cpp/blob/master/ggml/src/CMakeLists.txt
- llama.cpp build options: https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md
