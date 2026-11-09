#!/usr/bin/env python3
"""Offline tests for adding same-build ARM64 shared backend libs to an AAR."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import tempfile
import zipfile

SCRIPT = Path(__file__).with_name("package_vulkan_runtime.py")
spec = importlib.util.spec_from_file_location("package_vulkan_runtime", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        upstream = root / "upstream"
        build = upstream / "library" / ".cxx" / "RelWithDebInfo" / "arm64-v8a" / "obj"
        build.mkdir(parents=True)
        (build / "libggml-vulkan.so").write_bytes(b"test-vulkan-arm64")
        (build / "libggml-cpu.so").write_bytes(b"test-cpu-arm64")
        aar = root / "library-release.aar"
        with zipfile.ZipFile(aar, "w") as zf:
            zf.writestr("classes.jar", b"empty-jar-placeholder")
            for name in ("libllama_jni.so", "libllama.so", "libggml.so", "libggml-base.so"):
                zf.writestr(f"jni/arm64-v8a/{name}", b"existing-native-lib")

        original = module.is_arm64_elf
        module.is_arm64_elf = lambda _p: True
        try:
            added = module.patch_aar_with_build_backends(aar, upstream)
            assert "jni/arm64-v8a/libggml-vulkan.so" in added
            assert "jni/arm64-v8a/libggml-cpu.so" in added
            with zipfile.ZipFile(aar) as zf:
                names = set(zf.namelist())
                assert "jni/arm64-v8a/libggml-vulkan.so" in names
                assert "jni/arm64-v8a/libggml-cpu.so" in names
                assert zf.read("jni/arm64-v8a/libggml-vulkan.so") == b"test-vulkan-arm64"
                assert zf.read("jni/arm64-v8a/libggml-cpu.so") == b"test-cpu-arm64"
            # A second pass must not add duplicate zip entries.
            second = module.patch_aar_with_build_backends(aar, upstream)
            assert second == []
        finally:
            module.is_arm64_elf = original
    print("PASS: missing Vulkan/CPU shared objects are injected from same-build ARM64 outputs")
    print("PASS: already packaged shared objects are not duplicated")


if __name__ == "__main__":
    main()
