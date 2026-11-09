#!/usr/bin/env python3
"""Patch the actual Android JNI CMake root and Gradle args for Vulkan.

The upstream Gradle argument alone did not survive the Android CMake configure
(the user's cache still showed GGML_VULKAN=OFF).  We now force the cache values
in library/src/main/jni/CMakeLists.txt *before* llama.cpp is added as a
subdirectory.  This is the authoritative scope for the ggml options.

Backend mode: GGML_BACKEND_DL=OFF and BUILD_SHARED_LIBS=ON.  The ggml
backend target is an ordinary shared library (libggml-vulkan.so), linked to
libggml.so with DT_NEEDED; it is not a MODULE/dlopen plugin.  The Android
Vulkan loader remains a system library.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

GRADLE_REQUIRED = {
    "GGML_VULKAN": "ON",
    "GGML_BACKEND_DL": "OFF",
    "BUILD_SHARED_LIBS": "ON",
    "GGML_CPU": "ON",
}

GRADLE_EXTRA = {
    "Vulkan_INCLUDE_DIR": '"-DVulkan_INCLUDE_DIR=${System.getenv("VULKAN_INCLUDE_DIR") ?: ""}"',
    "Vulkan_LIBRARY": '"-DVulkan_LIBRARY=${System.getenv("VULKAN_LIBRARY") ?: ""}"',
    "Vulkan_GLSLC_EXECUTABLE": '"-DVulkan_GLSLC_EXECUTABLE=${System.getenv("VULKAN_GLSLC_EXECUTABLE") ?: ""}"',
    "SPIRV-Headers_DIR": '"-DSPIRV-Headers_DIR=${System.getenv("SPIRV_HEADERS_DIR") ?: ""}"',
}

CMAKE_MARKER_BEGIN = "# BEGIN TAPTRANSLATE VULKAN CONFIG"
CMAKE_MARKER_END = "# END TAPTRANSLATE VULKAN CONFIG"
GGML_MARKER_BEGIN = "# BEGIN TAPTRANSLATE FORCE GGML_VULKAN"
GGML_MARKER_END = "# END TAPTRANSLATE FORCE GGML_VULKAN"
CMAKE_BLOCK = '''# BEGIN TAPTRANSLATE VULKAN CONFIG
# Force these in the *parent* JNI CMake scope before add_subdirectory(llama.cpp).
# Gradle externalNativeBuild args alone were observed to leave GGML_VULKAN=OFF
# in the generated CMakeCache on the Android runner.
set(GGML_VULKAN ON CACHE BOOL "Enable ggml Vulkan backend" FORCE)
set(GGML_CPU ON CACHE BOOL "Keep ggml CPU backend as fallback" FORCE)
set(GGML_BACKEND_DL OFF CACHE BOOL "Link ggml backends normally, not via dlopen" FORCE)
set(BUILD_SHARED_LIBS ON CACHE BOOL "Build Android JNI dependency libraries as shared" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "Do not tune for the Linux build host" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "Avoid OpenMP runtime on Android" FORCE)
set(GGML_LLAMAFILE OFF CACHE BOOL "Disable unsupported llamafile backend on Android" FORCE)

if(DEFINED ENV{VULKAN_INCLUDE_DIR} AND NOT "$ENV{VULKAN_INCLUDE_DIR}" STREQUAL "")
    set(Vulkan_INCLUDE_DIR "$ENV{VULKAN_INCLUDE_DIR}" CACHE PATH "Khronos Vulkan-Headers C and C++ headers" FORCE)
    # Ensure Vulkan-Hpp is visible to ggml-vulkan.cpp even if a downstream
    # FindVulkan imported target omits or normalizes its include directories.
    include_directories(BEFORE SYSTEM "$ENV{VULKAN_INCLUDE_DIR}")
endif()
if(DEFINED ENV{VULKAN_LIBRARY} AND NOT "$ENV{VULKAN_LIBRARY}" STREQUAL "")
    set(Vulkan_LIBRARY "$ENV{VULKAN_LIBRARY}" CACHE FILEPATH "Android NDK Vulkan loader stub" FORCE)
endif()
if(DEFINED ENV{VULKAN_GLSLC_EXECUTABLE} AND NOT "$ENV{VULKAN_GLSLC_EXECUTABLE}" STREQUAL "")
    set(Vulkan_GLSLC_EXECUTABLE "$ENV{VULKAN_GLSLC_EXECUTABLE}" CACHE FILEPATH "Host glslc shader compiler" FORCE)
endif()
if(DEFINED ENV{SPIRV_HEADERS_DIR} AND NOT "$ENV{SPIRV_HEADERS_DIR}" STREQUAL "")
    set(SPIRV-Headers_DIR "$ENV{SPIRV_HEADERS_DIR}" CACHE PATH "SPIRV-Headers CMake package" FORCE)
endif()
if(DEFINED ENV{SPIRV_HEADERS_INCLUDE_DIR} AND NOT "$ENV{SPIRV_HEADERS_INCLUDE_DIR}" STREQUAL "")
    if(NOT EXISTS "$ENV{SPIRV_HEADERS_INCLUDE_DIR}/spirv/unified1/spirv.hpp")
        message(FATAL_ERROR "SPIRV-Headers include directory is missing spirv/unified1/spirv.hpp: $ENV{SPIRV_HEADERS_INCLUDE_DIR}")
    endif()
    set(SPIRV_HEADERS_INCLUDE_DIR "$ENV{SPIRV_HEADERS_INCLUDE_DIR}" CACHE PATH "SPIRV-Headers include directory" FORCE)
    # ggml-vulkan currently finds the SPIRV-Headers CMake package but does not
    # propagate its imported include directories to ggml-vulkan's compile
    # target on this Android build. Add the real header root to descendants.
    include_directories(BEFORE SYSTEM "$ENV{SPIRV_HEADERS_INCLUDE_DIR}")
    message(STATUS "TapTranslate: explicitly adding SPIRV-Headers include root: $ENV{SPIRV_HEADERS_INCLUDE_DIR}")
else()
    message(FATAL_ERROR "SPIRV_HEADERS_INCLUDE_DIR is required for ggml-vulkan (spirv/unified1/spirv.hpp)")
endif()
# END TAPTRANSLATE VULKAN CONFIG
'''


def option_matches(text: str, name: str) -> list[re.Match[str]]:
    pattern = re.compile(
        rf'(?<![A-Za-z0-9_])(?:"|-)?-D{re.escape(name)}=([A-Za-z0-9_./:$<>-]+)(?:")?'
    )
    return list(pattern.finditer(text))


def insert_options_into_list(text: str, options: list[str], path: Path) -> str:
    matches = list(re.finditer(r"(?m)^(?P<indent>[ \t]*)arguments\s*\+=\s*listOf\s*\(", text))
    if len(matches) != 1:
        preview = "\n".join(text.splitlines()[:140])
        raise RuntimeError(
            f"Could not identify a unique CMake `arguments += listOf(` block in {path}; "
            f"found {len(matches)}. First 140 lines:\n{preview}"
        )
    match = matches[0]
    indent = match.group("indent") + "    "
    payload = "\n" + "\n".join(f"{indent}{item}," for item in options)
    return text[:match.end()] + payload + text[match.end():]


def force_gradle_option(text: str, name: str, value: str, path: Path) -> str:
    found = option_matches(text, name)
    if len(found) > 1:
        raise RuntimeError(f"Found {len(found)} definitions of {name}; refusing ambiguous patch in {path}")
    if not found:
        return insert_options_into_list(text, [f'"-D{name}={value}"'], path)
    old = found[0].group(1)
    if old.upper() == value.upper():
        return text
    start, end = found[0].span(1)
    return text[:start] + value + text[end:]


def patch_gradle(upstream: Path) -> None:
    path = upstream / "library" / "build.gradle.kts"
    if not path.is_file():
        raise RuntimeError(f"Upstream Gradle file not found: {path}")
    original = path.read_text(encoding="utf-8")
    text = original
    for name, value in GRADLE_REQUIRED.items():
        text = force_gradle_option(text, name, value, path)
    missing: list[str] = []
    for name, argument in GRADLE_EXTRA.items():
        found = option_matches(text, name)
        if len(found) > 1:
            raise RuntimeError(f"Found {len(found)} definitions of {name}; refusing ambiguous patch")
        if not found:
            missing.append(argument)
    if missing:
        text = insert_options_into_list(text, missing, path)
    for name, expected in GRADLE_REQUIRED.items():
        found = option_matches(text, name)
        if len(found) != 1 or found[0].group(1).upper() != expected:
            raise RuntimeError(f"Gradle post-patch verification failed for {name}")
    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"Patched Gradle CMake args: {path}")
    else:
        print(f"Gradle CMake args already patched: {path}")


def patch_jni_cmake(upstream: Path) -> None:
    path = upstream / "library" / "src" / "main" / "jni" / "CMakeLists.txt"
    if not path.is_file():
        raise RuntimeError(f"Upstream JNI CMakeLists not found: {path}")
    original = path.read_text(encoding="utf-8")

    # Place the force block immediately before the add_subdirectory() that adds
    # the llama.cpp checkout. This is deliberately later than other upstream
    # configuration statements, so any earlier OFF cache assignment is replaced.
    add_pattern = r"(?ims)^[ \t]*add_subdirectory[ \t]*\((?:(?!\)).)*(?:llama\.cpp|LLAMA_CPP_DIR)(?:(?!\)).)*\)"
    add_matches = list(re.finditer(add_pattern, original))
    if len(add_matches) != 1:
        preview = "\n".join(original.splitlines()[:160])
        raise RuntimeError(
            f"Expected one add_subdirectory() call for the llama.cpp submodule in {path}, "
            f"found {len(add_matches)}. First 160 lines:\n{preview}"
        )

    # Remove any previous generated block before reinserting it at the exact
    # authoritative point. This also makes the operation safe across reruns.
    if CMAKE_MARKER_BEGIN in original or CMAKE_MARKER_END in original:
        if original.count(CMAKE_MARKER_BEGIN) != 1 or original.count(CMAKE_MARKER_END) != 1:
            raise RuntimeError(f"Duplicate/incomplete Vulkan patch markers in {path}")
        begin = original.index(CMAKE_MARKER_BEGIN)
        end = original.index(CMAKE_MARKER_END, begin) + len(CMAKE_MARKER_END)
        suffix = original[end:]
        # CMAKE_BLOCK ends with one newline and insertion adds one separator
        # newline. Consume exactly those two; preserve all original whitespace.
        if suffix.startswith("\r\n\r\n"):
            suffix = suffix[4:]
        elif suffix.startswith("\n\n"):
            suffix = suffix[2:]
        elif suffix.startswith("\n"):
            suffix = suffix[1:]
        original = original[:begin] + suffix
        add_matches = list(re.finditer(add_pattern, original))
        if len(add_matches) != 1:
            raise RuntimeError(f"After removing previous patch, llama.cpp add_subdirectory anchor is ambiguous in {path}")

    anchor = add_matches[0].start()
    text = original[:anchor] + CMAKE_BLOCK + "\n" + original[anchor:]

    # Verify placement and enforce options.
    block_pos = text.index(CMAKE_MARKER_BEGIN)
    subdir_pos = re.search(add_pattern, text)
    if not subdir_pos or block_pos >= subdir_pos.start():
        raise RuntimeError(f"Vulkan cache block is not immediately before the llama.cpp subdirectory in {path}")

    for expected in (
        "set(GGML_VULKAN ON CACHE BOOL",
        "set(GGML_CPU ON CACHE BOOL",
        "set(GGML_BACKEND_DL OFF CACHE BOOL",
        "set(BUILD_SHARED_LIBS ON CACHE BOOL",
    ):
        if expected not in text:
            raise RuntimeError(f"JNI CMake patch block is missing: {expected}")

    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"Patched authoritative JNI CMake scope immediately before llama.cpp: {path}")
    else:
        print(f"JNI CMake scope already patched: {path}")



def patch_ggml_vulkan_option(upstream: Path) -> None:
    """Force Vulkan at ggml's own option declaration in the pinned submodule.

    Gradle args and the parent JNI CMake cache block did not change the
    effective Android CMake cache in the user's previous run.  This patch
    therefore also adjusts the actual GGML_VULKAN option declaration and any
    explicit OFF assignments in the llama.cpp CMake source tree.
    """
    llama_root = upstream / "llama.cpp"
    if not llama_root.is_dir():
        raise RuntimeError(f"llama.cpp submodule not found: {llama_root}")

    cmake_files = sorted({*llama_root.rglob("CMakeLists.txt"), *llama_root.rglob("*.cmake")})
    cmake_files = [
        path for path in cmake_files
        if not any(part in {"build", ".cxx", "CMakeFiles"} for part in path.parts)
    ]
    if not cmake_files:
        raise RuntimeError(f"No CMakeLists.txt or .cmake files found under {llama_root}")

    marker_remove = re.compile(
        rf"(?ms)^[ \t]*{re.escape(GGML_MARKER_BEGIN)}\r?\n.*?^[ \t]*{re.escape(GGML_MARKER_END)}[ \t]*\r?\n(?:[ \t]*\r?\n)?"
    )
    # CMake assignments are code lines; comments are not matched.
    set_off_pattern = re.compile(
        r"(?im)^(?P<prefix>[ \t]*set\(\s*GGML_VULKAN\s+)OFF(?P<suffix>\s*(?:CACHE\s+BOOL\b[^)]*)?\))"
    )
    source_map: dict[Path, str] = {}
    force_off_count = 0
    for path in cmake_files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if GGML_MARKER_BEGIN in text or GGML_MARKER_END in text:
            if text.count(GGML_MARKER_BEGIN) != 1 or text.count(GGML_MARKER_END) != 1:
                raise RuntimeError(f"Duplicate/incomplete GGML Vulkan markers in {path}")
            text = marker_remove.sub("", text, count=1)
        text, changed_count = set_off_pattern.subn(
            lambda match: f"{match.group('prefix')}ON{match.group('suffix')}", text
        )
        force_off_count += changed_count
        source_map[path] = text

    option_pattern = re.compile(
        r"(?is)\boption\s*\(\s*GGML_VULKAN\b(?P<args>[^)]*)\)"
    )
    option_hits: list[tuple[Path, re.Match[str]]] = []
    for path, text in source_map.items():
        for match in option_pattern.finditer(text):
            option_hits.append((path, match))

    if len(option_hits) != 1:
        candidates: list[str] = []
        for path, text in source_map.items():
            for line_no, line in enumerate(text.splitlines(), 1):
                if "GGML_VULKAN" in line:
                    candidates.append(f"{path}:{line_no}: {line.strip()}")
        raise RuntimeError(
            "Expected exactly one option(GGML_VULKAN ...) declaration in the llama.cpp submodule, "
            f"found {len(option_hits)}. References:\n" + "\n".join(candidates[:120])
        )

    option_path, option_match = option_hits[0]
    text = source_map[option_path]
    args = option_match.group("args")
    default_match = re.search(
        r'(?is)(?P<description>\s*(?:"[^"]*"|[^\s)]+)\s+)(?P<default>ON|OFF)(?P<tail>\s*)$',
        args,
    )
    if not default_match:
        raise RuntimeError(
            f"Could not identify ON/OFF default in option(GGML_VULKAN ...) in {option_path}: {args!r}"
        )
    if default_match.group("default").upper() == "OFF":
        args_start, args_end = option_match.span("args")
        args = (
            args[:default_match.start("default")] + "ON" + args[default_match.end("default"):]
        )
        text = text[:args_start] + args + text[args_end:]
        option_match = option_pattern.search(text)
        assert option_match is not None

    force_block = (
        f"{GGML_MARKER_BEGIN}\n"
        "# Force Vulkan at ggml's own option declaration, after parent project setup.\n"
        'set(GGML_VULKAN ON CACHE BOOL "Enable ggml Vulkan backend for Android" FORCE)\n'
        f"{GGML_MARKER_END}\n\n"
    )
    option_start = option_match.start()
    text = text[:option_start] + force_block + text[option_start:]

    # Verify this is the actual ggml option declaration, with an ON default,
    # and that no CMake source in the submodule can subsequently set it OFF.
    final_option = option_pattern.search(text)
    if not final_option or text.index(GGML_MARKER_BEGIN) >= final_option.start():
        raise RuntimeError(f"GGML Vulkan force block was not placed before option() in {option_path}")
    final_default = re.search(
        r'(?is)(?P<description>\s*(?:"[^"]*"|[^\s)]+)\s+)(?P<default>ON|OFF)(?P<tail>\s*)$',
        final_option.group("args"),
    )
    if not final_default or final_default.group("default").upper() != "ON":
        raise RuntimeError(f"GGML_VULKAN option default is not ON after patch in {option_path}")
    for path, source in source_map.items():
        checked = text if path == option_path else source
        if set_off_pattern.search(checked):
            raise RuntimeError(f"A GGML_VULKAN=OFF CMake assignment remains in {path}")

    source_map[option_path] = text
    changed_files = [path for path, source in source_map.items() if path.read_text(encoding="utf-8") != source]
    for path in changed_files:
        path.write_text(source_map[path], encoding="utf-8")
    print(
        f"Patched ggml's actual option declaration: {option_path}; "
        f"removed/changed GGML_VULKAN OFF assignments: {force_off_count}; "
        f"changed CMake files: {len(changed_files)}"
    )


def patch_upstream(upstream: Path) -> int:
    try:
        patch_gradle(upstream)
        patch_jni_cmake(upstream)
        patch_ggml_vulkan_option(upstream)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    print("Verified authoritative Vulkan configuration at Gradle, JNI CMake, and the actual ggml option declaration:")
    print("  GGML_VULKAN=ON (CACHE BOOL FORCE)")
    print("  GGML_CPU=ON (CPU fallback kept)")
    print("  GGML_BACKEND_DL=OFF")
    print("  BUILD_SHARED_LIBS=ON")
    print("  Khronos Vulkan-Headers (vulkan.hpp) + SPIRV-Headers (spirv.hpp) + Android NDK Vulkan loader stub + host glslc")
    print("Expected link mode: libggml-vulkan.so and libggml-cpu*.so are ordinary shared dependencies of libggml.so; GGML_BACKEND_DL remains OFF (no dlopen plugin scan).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("upstream", type=Path)
    args = ap.parse_args()
    return patch_upstream(args.upstream)


def main_for_test(upstream: Path) -> int:
    return patch_upstream(upstream)


if __name__ == "__main__":
    raise SystemExit(main())
