#!/usr/bin/env python3
"""Patch llama-android's CMake arguments for a linked Android Vulkan backend.

Important: GGML_BACKEND_DL requires BUILD_SHARED_LIBS. For this Android AAR we
intentionally do NOT use the dlopen backend mode. Instead, CMake builds the CPU
and Vulkan backends as regular shared libraries and links them to libggml.so.
This lets Android's normal native dependency loader register Vulkan directly,
without relying on llama.cpp's executable-directory plugin search on Android.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


EXTRA_OPTIONS = {
    "Vulkan_INCLUDE_DIR": '"-DVulkan_INCLUDE_DIR=${System.getenv("VULKAN_INCLUDE_DIR") ?: ""}"',
    "Vulkan_LIBRARY": '"-DVulkan_LIBRARY=${System.getenv("VULKAN_LIBRARY") ?: ""}"',
    "Vulkan_GLSLC_EXECUTABLE": '"-DVulkan_GLSLC_EXECUTABLE=${System.getenv("VULKAN_GLSLC_EXECUTABLE") ?: ""}"',
    "SPIRV-Headers_DIR": '"-DSPIRV-Headers_DIR=${System.getenv("SPIRV_HEADERS_DIR") ?: ""}"',
}

REQUIRED_OPTIONS = {
    "GGML_VULKAN": "ON",
    "GGML_BACKEND_DL": "OFF",
    "BUILD_SHARED_LIBS": "ON",
}


def option_matches(text: str, name: str) -> list[re.Match[str]]:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_])(?:\"|-)?-D{re.escape(name)}=([A-Za-z0-9_./:$<>-]+)(?:\")?"
    )
    return list(pattern.finditer(text))


def insert_options_into_list(text: str, options: list[str], path: Path) -> str:
    matches = list(re.finditer(r"(?m)^(?P<indent>[ \t]*)arguments\s*\+=\s*listOf\s*\(", text))
    if len(matches) != 1:
        preview = "\n".join(text.splitlines()[0:140])
        raise RuntimeError(
            f"Could not identify a unique CMake `arguments += listOf(` block in {path}; "
            f"found {len(matches)}. Upstream Gradle structure changed. First 140 lines:\n{preview}"
        )
    match = matches[0]
    indent = match.group("indent") + "    "
    payload = "\n" + "\n".join(f"{indent}{item}," for item in options)
    return text[:match.end()] + payload + text[match.end():]


def force_option(text: str, name: str, value: str, path: Path) -> str:
    found = option_matches(text, name)
    if len(found) > 1:
        raise RuntimeError(f"Found {len(found)} definitions of {name}; refusing ambiguous patch in {path}")
    if not found:
        return insert_options_into_list(text, [f'"-D{name}={value}"'], path)
    match = found[0]
    old = match.group(1)
    if old.upper() == value.upper():
        return text
    start, end = match.span(1)
    return text[:start] + value + text[end:]


def patch_upstream(upstream: Path) -> int:
    path = upstream / "library" / "build.gradle.kts"
    if not path.is_file():
        print(f"ERROR: upstream Gradle file not found: {path}", file=sys.stderr)
        return 2

    original = path.read_text(encoding="utf-8")
    text = original
    try:
        for name, value in REQUIRED_OPTIONS.items():
            text = force_option(text, name, value, path)

        missing: list[str] = []
        for name, argument in EXTRA_OPTIONS.items():
            found = option_matches(text, name)
            if len(found) > 1:
                raise RuntimeError(f"Found {len(found)} definitions of {name}; refusing ambiguous patch")
            if not found:
                missing.append(argument)
        if missing:
            text = insert_options_into_list(text, missing, path)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    for name, expected in REQUIRED_OPTIONS.items():
        found = option_matches(text, name)
        if len(found) != 1 or found[0].group(1).upper() != expected:
            print(f"ERROR: post-patch verification failed for {name}", file=sys.stderr)
            return 4

    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"Patched {path}")
    else:
        print(f"Already patched: {path}")

    print("Verified CMake options:")
    for name, value in REQUIRED_OPTIONS.items():
        print(f"  {name}={value}")
    print("  Android Vulkan headers/loader stub and host glslc/SPIR-V headers are passed from GitHub Actions.")
    print("  Vulkan and CPU are regular shared backends linked into libggml.so; backend dlopen is disabled.")
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
