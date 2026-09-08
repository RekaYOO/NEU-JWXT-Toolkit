import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def recipe(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "packaging/android/recipes/neu_android_backend.py"
    spec = importlib.util.spec_from_file_location("neu_recipe_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "neu_android_backend.py"))
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setenv("CIBW_HOST_TRIPLET", "aarch64-linux-android")
    monkeypatch.setenv("CC", "/ndk/aarch64-linux-android24-clang")
    monkeypatch.setattr(module, "sysconfig", SimpleNamespace(
        get_config_var=lambda key: {"ANDROID_API_LEVEL": 24, "LIBDIR": "/target/python/lib"}[key],
    ))
    return module


def test_rust_recipe_uses_target_python_and_ndk_linker(recipe, tmp_path):
    (tmp_path / "Cargo.toml").touch()
    recipe._configure()
    assert os.environ["CARGO_BUILD_TARGET"] == "aarch64-linux-android"
    assert os.environ["CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER"] == "/ndk/aarch64-linux-android24-clang"
    assert os.environ["PYO3_CROSS_LIB_DIR"] == "/target/python/lib"
    assert os.environ["PYO3_CROSS_PYTHON_VERSION"] == "3.13"
    assert "max-page-size=16384" in os.environ["RUSTFLAGS"]


def test_lxml_recipe_cross_configures_pinned_static_dependencies(recipe, tmp_path, monkeypatch):
    (tmp_path / "buildlibxml.py").touch()
    build = Mock()
    build._neu_cross = False
    libraries = SimpleNamespace(cmmi=build)
    monkeypatch.setattr(recipe, "importlib", SimpleNamespace(import_module=lambda name: libraries))
    monkeypatch.setattr(recipe, "subprocess", SimpleNamespace(check_output=lambda *args, **kwargs: "x86_64-linux-gnu"))
    recipe._configure()
    libraries.cmmi(["./configure"], "/tmp/libxml2-2.15.2")
    assert "--host=aarch64-linux-android" in build.call_args.args[0]
    assert "--build=x86_64-linux-gnu" in build.call_args.args[0]
    libraries.cmmi(["./configure"], "/tmp/zlib-1.3.2")
    assert build.call_args.args[0] == ["./configure", "--static"]
    assert os.environ["LIBXSLT_VERSION"] == "1.1.45"
    assert os.environ["STATIC_DEPS"] == "true"


def test_recipe_rejects_host_build_accident(recipe, monkeypatch):
    monkeypatch.delenv("CIBW_HOST_TRIPLET")
    with pytest.raises(RuntimeError, match="cross environment"):
        recipe._configure()
