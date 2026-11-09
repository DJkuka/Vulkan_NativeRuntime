# Hy-MT2 Vulkan native runtime для Android ARM64

## Это именно сборка native runtime, а не отдельное диагностическое приложение

Проект пересобирает **исходную библиотеку `dev.ffmpegkit-maintained:llama-android:0.1.1`** с включённым Vulkan backend llama.cpp. В upstream CPU/NEON-варианте явно задано `-DGGML_VULKAN=OFF`; здесь этот параметр заменяется на `-DGGML_VULKAN=ON`, а Vulkan backend статически включается в JNI runtime.

Используется тот же API `dev.ffmpegkit.llama.Llama` / `LlamaConfig`, что и в уже проверенной Hy-MT2-интеграции. Это позволяет проверить именно новый движок, не переписывая Kotlin-обвязку и не затрагивая Tap Translate OCR/Tesseract/AutoFill/Google Offline Translate.

**Что появится после GitHub Actions:**

- `llama-android-vulkan.aar` — AAR с тем же Kotlin API и Vulkan-enabled native runtime;
- `libllama_jni.so` и все остальные ARM64 `.so`, входящие в AAR;
- `HyMT2-Vulkan-NativeRuntime.zip` с нативными библиотеками, AAR и manifest сборки;
- отдельный artifact с AAR для прямой подмены CPU AAR в тестовой helper-сборке.

Модель GGUF в проект и AAR не включается.

## Откуда берётся исходный код

Workflow клонирует публичный репозиторий `ffmpegkit-maintained/llama-android` с его зафиксированным git submodule `llama.cpp`. В README upstream библиотека указывает сборку llama.cpp `b9878`, ABI `arm64-v8a`, а CMake параметры `GGML_VULKAN=OFF` (Free CPU/NEON). Здесь меняется только native build-конфигурация: включается Vulkan, выключается динамическая загрузка backend, чтобы Vulkan backend был статически привязан к JNI native library.

Исходный Kotlin API, JNI bridge, пакет классов и остальная логика upstream-библиотеки не переписываются.

## Сборка

1. Создай **отдельный** GitHub-репозиторий для этого теста.
2. Загрузи в него содержимое архива, сохранив `.github/workflows`, `scripts` и `README_RU.md`.
3. Сделай commit в `main`.
4. Открой **Actions → Build llama-android Hy-MT2 Vulkan native runtime → Run workflow**.
5. После успешной сборки скачай:
   - `HyMT2-Vulkan-NativeRuntime-ARM64` — основной архив native runtime;
   - `HyMT2-Vulkan-AAR-ARM64` — отдельный AAR.

Workflow сам:

1. Устанавливает JDK 17, Android SDK, NDK r27c и CMake 3.22.1 (это версии, ожидаемые upstream `llama-android`).
2. Устанавливает host `glslc` и SPIR-V headers для компиляции Vulkan shader pipeline.
3. Клонирует upstream `llama-android` вместе с submodule `llama.cpp`.
4. Проверяет, что в upstream build file найден исходный параметр `GGML_VULKAN=OFF`, и заменяет его на `ON`.
5. Передаёт CMake путь к **Android NDK Vulkan headers/loader stub** и host `glslc`/SPIR-V headers.
6. Собирает `:library:assembleRelease` — именно AAR, а не самописный тестовый APK.
7. Проверяет CMake cache на `GGML_VULKAN=ON`, ARM64 ELF-библиотеки, зависимость runtime от системного `libvulkan.so`, наличие JNI bridge и отсутствие упакованного внутрь AAR `libvulkan.so`.
8. Извлекает native `.so` из AAR, считает SHA-256 и создаёт runtime ZIP.

## Как подключить runtime для теста

В тестовом Gradle-проекте, где сейчас указано:

```gradle
implementation("dev.ffmpegkit-maintained:llama-android:0.1.1")
```

положи `llama-android-vulkan.aar` в `app/libs/` и временно замени зависимость на:

```gradle
implementation(files("libs/llama-android-vulkan.aar"))
```

API пакета `dev.ffmpegkit.llama` оставлен upstream-совместимым. Остальной Kotlin-код загрузки Hy-MT2 и вызова `Llama.complete(...)` менять не требуется.

В `LlamaConfig` есть параметр `gpuLayers`: `0` означает CPU-only, значение больше нуля запрашивает offload слоёв на GPU. Например:

```kotlin
val cpuConfig = LlamaConfig(contextSize = 2048, threads = 4, gpuLayers = 0)
val vulkanConfig = LlamaConfig(contextSize = 2048, threads = 4, gpuLayers = 2)
```

Проведи один и тот же короткий перевод в двух конфигурациях. Для Vulkan начни с `gpuLayers = 2`, затем проверь `4` и `8`, каждый раз выгружая модель между запусками. Важно: этот AAR включает Vulkan backend, но конкретный `Adreno 610` ещё должен пройти runtime-тест. Сам факт успешной компиляции не доказывает, что все Vulkan kernels и memory allocation стабильно работают на драйвере планшета.

## Что означает успешная сборка

Сборка считается Vulkan-сборкой только если одновременно выполнены условия:

- upstream CMake cache содержит `GGML_VULKAN:BOOL=ON`;
- цель `ggml-vulkan` присутствует в generated CMake/Ninja build graph;
- в собранном native runtime есть Vulkan code/backend;
- native ELF динамически ссылается на системный `libvulkan.so` (сам loader не копируется в AAR);
- AAR содержит `libllama_jni.so` для `arm64-v8a`.

Если хотя бы одна проверка не прошла, GitHub Actions завершится ошибкой и не опубликует runtime artifact как успешный.

## Ограничения

- Я подготовил исходный проект и workflow, но не запускал GitHub Actions из твоего репозитория и не могу утверждать, что AAR уже собран на runner.
- Физическую работу на Lenovo TB128FU / Adreno 610 можно подтвердить только запуском с GGUF на самом планшете.
- `libvulkan.so` берётся из Android. Не нужно и нельзя подменять его библиотекой, собранной для Linux/runner.
- Этот этап — сборка и проверка native runtime. После реального теста на планшете можно перенести тот же AAR в основной Hy-MT2 проект и добавить отдельный переключатель Vulkan в настройках приложения.

## Источники

- Upstream library и описание CPU/Vulkan вариантов: https://github.com/ffmpegkit-maintained/llama-android
- Native build configuration upstream: https://github.com/ffmpegkit-maintained/llama-android/blob/main/library/build.gradle.kts
- llama.cpp Vulkan build options: https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md
