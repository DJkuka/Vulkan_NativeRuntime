#!/usr/bin/env python3
"""Assert the Android NDK smoke compile command uses both staged Khronos headers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compile-commands", required=True, type=Path)
    parser.add_argument("--vulkan-include-dir", required=True)
    parser.add_argument("--spirv-include-dir", required=True)
    args = parser.parse_args()

    if not args.compile_commands.is_file():
        raise SystemExit(f"compile_commands.json not found: {args.compile_commands}")

    entries = json.loads(args.compile_commands.read_text(encoding="utf-8"))
    relevant = [
        entry for entry in entries
        if Path(entry.get("file", "")).name == "android_vulkan_header_smoke.cpp"
    ]
    if len(relevant) != 1:
        raise SystemExit(
            "Expected exactly one Android Vulkan header smoke compile command; "
            f"found {len(relevant)} in {args.compile_commands}"
        )

    entry = relevant[0]
    if isinstance(entry.get("arguments"), list):
        command = " ".join(entry["arguments"])
    else:
        command = entry.get("command", "")

    missing = [
        label for label, path in (
            ("Vulkan-Headers", args.vulkan_include_dir),
            ("SPIRV-Headers", args.spirv_include_dir),
        ) if str(Path(path).resolve()) not in command
    ]
    if missing:
        print("Android smoke compiler command:", command, file=sys.stderr)
        raise SystemExit(
            "Android smoke target is missing include path(s): " + ", ".join(missing)
        )

    spirv = str(Path(args.spirv_include_dir).resolve())
    if spirv == "/usr/include" or spirv.startswith("/usr/include/"):
        raise SystemExit("SPIRV-Headers path resolves to host /usr/include; refusing mixed host/Android includes")

    print("PASS: Android CMake smoke compile command contains Vulkan-Headers and staged SPIRV-Headers")
    print(f"  Vulkan-Headers: {Path(args.vulkan_include_dir).resolve()}")
    print(f"  SPIRV-Headers: {Path(args.spirv_include_dir).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
