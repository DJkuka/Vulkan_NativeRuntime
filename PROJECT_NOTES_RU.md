# Технические заметки — Vulkan Native Runtime v5

- Основа: upstream `ffmpegkit-maintained/llama-android`, pinned `llama.cpp` submodule `b9878`.
- Конфигурация: `GGML_VULKAN=ON`, `BUILD_SHARED_LIBS=ON`, `GGML_BACKEND_DL=OFF`.
- Почему так: CMake ggml прямо указывает, что `GGML_BACKEND_DL` требует `BUILD_SHARED_LIBS`; динамические plugins типа `MODULE` в upstream пишутся в CMAKE_RUNTIME_OUTPUT_DIRECTORY и не всегда автоматически попадают в Android AAR. Для Android мы отключаем plugin dlopen mode и собираем backend targets как обычные shared libraries, связанные через `libggml.so`.
- Expected ELF chain: `libggml.so` → `libggml-vulkan.so` and `libggml-cpu.so`; `libggml-vulkan.so` → Android system `libvulkan.so`.
- Packaging: сначала проверить AAR jni entries, затем при необходимости добавить missing shared backend outputs, взятые строго из того же upstream CMake build и прошедшие ELF AArch64 check. Не копировать host/Linux .so и никогда не упаковывать system `libvulkan.so`.
- Outputs: Vulkan-enabled AAR, standalone ARM64 .so bundle, manifest with CMake cache/Gradle args/ELF deps and SHA-256.
- No model GGUF, APK signing key, or Tap Translate base APK included.

## История исправлений

- v1: initial patch attempt was too strict about upstream Gradle formatting.
- v2: match CMake options by name and make patch idempotent.
- v3: broaden CMake cache/Ninja graph discovery.
- v4: tried `GGML_BACKEND_DL=ON` with `BUILD_SHARED_LIBS=OFF`; this was an invalid/contradictory configuration for ggml backend modules and did not package `libggml-vulkan.so` in AAR.
- v5: use shared-linked backend mode. Patcher enforces the three CMake values; packaging step collects missing backend .so from this same CMake output and injects them into canonical AAR `jni/arm64-v8a/`, then verifies the ELF dependency chain.
