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


def test_lxml_config_commands_explicitly_use_host_shell(recipe, monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0, stderr="", stdout="2.15.2\n"))
    monkeypatch.setattr(recipe, "subprocess", SimpleNamespace(run=run))
    assert recipe._host_config_command("/build/xml2-config", "--version") == "2.15.2"
    assert run.call_args.args == ("/build/xml2-config --version",)
    assert run.call_args.kwargs["executable"] == "/bin/sh"
    assert recipe._host_config_command("") == ""
    run.assert_called_once()


@pytest.fixture
def pure_recipe():
    path = Path(__file__).resolve().parents[2] / "packaging/android/recipes/patch_pure_wheels.py"
    spec = importlib.util.spec_from_file_location("neu_pure_recipe_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_android_uvicorn_import_does_not_require_multiprocessing(tmp_path, pure_recipe):
    import subprocess
    import sys
    import uvicorn

    source = Path(uvicorn.__file__).read_text(encoding="utf-8")
    initializer = tmp_path / "__init__.py"
    initializer.write_text(pure_recipe.uvicorn_initializer(source), encoding="utf-8")
    script = """
import importlib.abc, importlib.util, sys
class NoMultiprocessing(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "_multiprocessing":
            raise ModuleNotFoundError("Android does not provide _multiprocessing")
sys.meta_path.insert(0, NoMultiprocessing())
spec = importlib.util.spec_from_file_location("uvicorn", sys.argv[1],
    submodule_search_locations=[sys.argv[2]])
module = importlib.util.module_from_spec(spec)
sys.modules["uvicorn"] = module
spec.loader.exec_module(module)
assert module.Config and module.Server
assert "uvicorn.main" not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", script, str(initializer), str(Path(uvicorn.__file__).parent)],
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_android_pure_wheel_patch_updates_record_and_preserves_version(tmp_path, pure_recipe):
    import base64
    import csv
    import hashlib
    import io
    import zipfile

    wheel = tmp_path / "uvicorn-0.51.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("uvicorn/__init__.py",
                         "from uvicorn.config import Config\nfrom uvicorn.main import Server, main, run\n")
        archive.writestr("uvicorn-0.51.0.dist-info/METADATA", "Version: 0.51.0\n")
        archive.writestr("uvicorn-0.51.0.dist-info/RECORD", "")
    pure_recipe.patch_uvicorn(wheel)
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("uvicorn-0.51.0.dist-info/METADATA") == b"Version: 0.51.0\n"
        records = csv.reader(io.StringIO(archive.read("uvicorn-0.51.0.dist-info/RECORD").decode()))
        for name, digest, size in records:
            if digest:
                content = archive.read(name)
                assert digest == "sha256=" + base64.urlsafe_b64encode(
                    hashlib.sha256(content).digest()).rstrip(b"=").decode()
                assert int(size) == len(content)
