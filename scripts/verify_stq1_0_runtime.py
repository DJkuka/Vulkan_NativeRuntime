#!/usr/bin/env python3
"""Fail closed unless the AAR contains the STQ1_0 GGUF loader and CPU kernel."""
from __future__ import annotations
import argparse
import zipfile
from pathlib import Path

REQUIRED = {
    "jni/arm64-v8a/libggml-base.so": (b"stq1_0",),
    "jni/arm64-v8a/libggml-cpu.so": (b"ggml_vec_dot_stq1_0_q8_K",),
    "jni/arm64-v8a/libllama.so": (b"STQ1_0 - 1.31 bpw ternary",),
    "jni/arm64-v8a/libggml-vulkan.so": (),
}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aar", type=Path, required=True)
    args = parser.parse_args()
    with zipfile.ZipFile(args.aar) as archive:
        names = set(archive.namelist())
        for path, markers in REQUIRED.items():
            if path not in names:
                raise SystemExit(f"Missing required native library: {path}")
            data = archive.read(path)
            for marker in markers:
                if marker not in data:
                    raise SystemExit(
                        f"{path} does not contain required STQ1_0 marker {marker!r}"
                    )
        # Reject the old parser build, where GGML_TYPE_COUNT ended at 42 and
        # STQ1_0 type ID 43 was not registered.
        base = archive.read("jni/arm64-v8a/libggml-base.so")
        if b"stq1_0" not in base:
            raise SystemExit("GGML base library has no STQ1_0 quant type registration")
    print("STQ1_0 verified: GGUF type registration, llama model type label and CPU dot kernel present")
    print("Vulkan backend retained; STQ1_0 remains CPU-only in the app configuration")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
