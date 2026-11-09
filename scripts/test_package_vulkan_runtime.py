#!/usr/bin/env python3
"""Regression tests for cache/graph checks and same-build AAR backend injection."""
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
    good_cache = """GGML_VULKAN:BOOL=ON\nGGML_CPU:BOOL=ON\nGGML_BACKEND_DL:BOOL=OFF\nBUILD_SHARED_LIBS:BOOL=ON\n"""
    bad_cache = good_cache.replace("GGML_VULKAN:BOOL=ON", "GGML_VULKAN:BOOL=OFF")
    assert module.parse_cache_values(good_cache) == module.REQUIRED_CACHE
    assert module.parse_cache_values(bad_cache)["GGML_VULKAN"] == "OFF"

    graph = (
        "build ggml/src/ggml-vulkan/CMakeFiles/ggml-vulkan.dir/ggml-vulkan.cpp.o: CXX_COMPILER\n"
        "build ggml/src/ggml-cpu/CMakeFiles/ggml-cpu.dir/ggml-cpu.c.o: C_COMPILER\n"
    )
    assert module.graph_has_backend(graph, "ggml-vulkan")
    assert module.graph_has_backend(graph, "ggml-cpu")
    assert not module.graph_has_backend("build ggml/src/ggml-cpu/a.o: C_COMPILER", "ggml-vulkan")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        upstream = root / "upstream"
        build_dir = upstream / "library" / ".cxx" / "RelWithDebInfo" / "arm64-v8a" / "obj"
        build_dir.mkdir(parents=True)
        (build_dir / "libggml-vulkan.so").write_bytes(b"fake-arm64-vulkan-so")
        (build_dir / "libggml-cpu.so").write_bytes(b"fake-arm64-cpu-so")
        aar = root / "library-release.aar"
        with zipfile.ZipFile(aar, "w") as archive:
            archive.writestr("classes.jar", b"placeholder")
            for name in ("libllama_jni.so", "libllama.so", "libggml.so", "libggml-base.so"):
                archive.writestr(f"jni/arm64-v8a/{name}", b"existing-native")

        original = module.is_arm64_elf
        module.is_arm64_elf = lambda _path: True
        try:
            added = module.patch_aar_with_build_backends(aar, upstream)
            assert "jni/arm64-v8a/libggml-vulkan.so" in added
            assert "jni/arm64-v8a/libggml-cpu.so" in added
            with zipfile.ZipFile(aar) as archive:
                names = set(archive.namelist())
                assert "jni/arm64-v8a/libggml-vulkan.so" in names
                assert "jni/arm64-v8a/libggml-cpu.so" in names
                assert archive.read("jni/arm64-v8a/libggml-vulkan.so") == b"fake-arm64-vulkan-so"
                assert archive.read("jni/arm64-v8a/libggml-cpu.so") == b"fake-arm64-cpu-so"
            second = module.patch_aar_with_build_backends(aar, upstream)
            assert second == []
        finally:
            module.is_arm64_elf = original

    print("PASS: effective CMakeCache ON/OFF parsing")
    print("PASS: Ninja graph requires Vulkan and CPU backend targets")
    print("PASS: missing ARM64 backend .so files are injected from the same build")
    print("PASS: already packaged backends are not duplicated on rerun")


if __name__ == "__main__":
    main()
