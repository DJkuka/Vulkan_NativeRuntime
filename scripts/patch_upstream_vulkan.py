#!/usr/bin/env python3
"""Enable the llama.cpp Vulkan backend in the upstream llama-android Gradle build.

The upstream Gradle file is allowed to change comments/formatting.  We therefore
patch CMake arguments by option name, not by matching an entire source line.
The script is idempotent: running it twice does not duplicate CMake arguments.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


# Extra CMake arguments needed by the Android Vulkan build. Values are read by
# Gradle from the GitHub Actions environment, which is populated by the workflow.
EXTRA_OPTIONS = {
    "GGML_BACKEND_DL": '"-DGGML_BACKEND_DL=OFF"',
    "BUILD_SHARED_LIBS": '"-DBUILD_SHARED_LIBS=OFF"',
    "Vulkan_INCLUDE_DIR": '"-DVulkan_INCLUDE_DIR=${System.getenv("VULKAN_INCLUDE_DIR") ?: ""}"',
    "Vulkan_LIBRARY": '"-DVulkan_LIBRARY=${System.getenv("VULKAN_LIBRARY") ?: ""}"',
    "Vulkan_GLSLC_EXECUTABLE": '"-DVulkan_GLSLC_EXECUTABLE=${System.getenv("VULKAN_GLSLC_EXECUTABLE") ?: ""}"',
    "SPIRV-Headers_DIR": '"-DSPIRV-Headers_DIR=${System.getenv("SPIRV_HEADERS_DIR") ?: ""}"',
}


def option_matches(text: str, name: str) -> list[re.Match[str]]:
    # Accept e.g. "-DGGML_VULKAN=OFF", '-DGGML_VULKAN=ON', or an unquoted token.
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_])(?:\"|-)?-D{re.escape(name)}=([A-Za-z0-9_./:$<>-]+)(?:\")?"
    )
    return list(pattern.finditer(text))


def insert_options_into_list(text: str, options: list[str], path: Path) -> str:
    """Insert options into the first Gradle CMake `arguments += listOf(` block."""
    # Target the CMake arguments list, not arbitrary Kotlin listOf() calls.
    matches = list(re.finditer(r"(?m)^(?P<indent>[ \t]*)arguments\s*\+=\s*listOf\s*\(", text))
    if len(matches) != 1:
        preview = "\n".join(text.splitlines()[0:120])
        raise RuntimeError(
            f"Could not identify a unique CMake `arguments += listOf(` block in {path}; "
            f"found {len(matches)}. Upstream Gradle structure changed. First 120 lines:\n{preview}"
        )

    match = matches[0]
    indent = match.group("indent") + "    "
    payload = "\n" + "\n".join(f"{indent}{item}," for item in options)
    return text[: match.end()] + payload + text[match.end() :]


def patch_upstream(upstream: Path) -> int:
    path = upstream / "library" / "build.gradle.kts"
    if not path.is_file():
        print(f"ERROR: upstream Gradle file not found: {path}", file=sys.stderr)
        return 2

    original = path.read_text(encoding="utf-8")
    text = original

    vulkan_matches = option_matches(text, "GGML_VULKAN")
    if len(vulkan_matches) > 1:
        print(
            f"ERROR: found {len(vulkan_matches)} GGML_VULKAN arguments; refusing ambiguous patch",
            file=sys.stderr,
        )
        return 3

    if vulkan_matches:
        match = vulkan_matches[0]
        if match.group(1).upper() != "ON":
            # Change only the option's value; preserve quote style, comma and comments.
            value_start, value_end = match.span(1)
            text = text[:value_start] + "ON" + text[value_end:]
    else:
        # Some upstream commits may omit the option entirely. Add it to the actual
        # CMake argument list instead of assuming a particular comment or line.
        try:
            text = insert_options_into_list(text, ['"-DGGML_VULKAN=ON"'], path)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 4

    # Add supporting CMake arguments only when they are not already configured.
    missing: list[str] = []
    for name, argument in EXTRA_OPTIONS.items():
        found = option_matches(text, name)
        if len(found) > 1:
            print(f"ERROR: found {len(found)} definitions of {name}; refusing ambiguous patch", file=sys.stderr)
            return 5
        if not found:
            missing.append(argument)

    if missing:
        try:
            text = insert_options_into_list(text, missing, path)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 6

    # Fail closed if the expected configuration cannot be read back unambiguously.
    for name, expected in {
        "GGML_VULKAN": "ON",
        "GGML_BACKEND_DL": "OFF",
        "BUILD_SHARED_LIBS": "OFF",
    }.items():
        found = option_matches(text, name)
        if len(found) != 1 or found[0].group(1).upper() != expected:
            print(f"ERROR: post-patch verification failed for {name}; found {len(found)} definitions", file=sys.stderr)
            return 7

    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"Patched {path}")
    else:
        print(f"Already patched: {path}")

    print("Verified CMake options:")
    for name in ("GGML_VULKAN", "GGML_BACKEND_DL", "BUILD_SHARED_LIBS"):
        match = option_matches(text, name)[0]
        print(f"  {name}={match.group(1).upper()}")
    for name in ("Vulkan_INCLUDE_DIR", "Vulkan_LIBRARY", "Vulkan_GLSLC_EXECUTABLE", "SPIRV-Headers_DIR"):
        if option_matches(text, name):
            print(f"  {name}=<provided from GitHub Actions environment>")
    print("Android NDK Vulkan headers/loader stub and host glslc/SPIRV-Headers are passed to CMake.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("upstream", type=Path, help="Root of cloned llama-android repository")
    args = ap.parse_args()
    return patch_upstream(args.upstream)


def main_for_test(upstream: Path) -> int:
    """Test entry point that avoids argparse and uses a temporary upstream tree."""
    return patch_upstream(upstream)


if __name__ == "__main__":
    raise SystemExit(main())
