"""PEP 517 wrapper for the locked Android native-extension sources.

Loaded inside cibuildwheel's Android cross environment, never the host interpreter
used to install build requirements. Upstream package code and versions are unchanged.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sysconfig


def _configure() -> None:
    triplet = os.environ.get("CIBW_HOST_TRIPLET")
    if triplet not in {"aarch64-linux-android", "x86_64-linux-android"}:
        raise RuntimeError("Native recipes require cibuildwheel's Android cross environment")
    if sysconfig.get_config_var("ANDROID_API_LEVEL") != 24:
        raise RuntimeError("Android dependencies must target API 24")
    root = Path(__file__).resolve().parent
    os.environ["CFLAGS"] = os.environ.get("CFLAGS", "") + " -fPIC"
    os.environ["LDFLAGS"] = os.environ.get("LDFLAGS", "") + " -Wl,-z,max-page-size=16384"
    if (root / "Cargo.toml").is_file():
        compiler = shlex.split(os.environ["CC"])
        if len(compiler) != 1:
            raise RuntimeError("The NDK compiler must be an executable path, not a shell command")
        os.environ.update({
            "CARGO_BUILD_TARGET": triplet,
            f"CARGO_TARGET_{triplet.upper().replace('-', '_')}_LINKER": compiler[0],
            "PYO3_CROSS": "1",
            "PYO3_CROSS_PYTHON_VERSION": "3.13",
            "PYO3_CROSS_LIB_DIR": str(sysconfig.get_config_var("LIBDIR")),
        })
        os.environ["RUSTFLAGS"] = os.environ.get("RUSTFLAGS", "") + " -C link-arg=-Wl,-z,max-page-size=16384"

    if (root / "buildlibxml.py").is_file():
        os.environ.update({
            "STATIC_DEPS": "true",
            "LIBXML2_VERSION": "2.15.2",
            "LIBXSLT_VERSION": "1.1.45",
            "LIBICONV_VERSION": "1.19",
            "ZLIB_VERSION": "1.3.2",
        })
        libraries = importlib.import_module("buildlibxml")
        original = libraries.cmmi
        if not getattr(original, "_neu_cross", False):
            build_triplet = subprocess.check_output(["gcc", "-dumpmachine"], text=True).strip()

            def cross_cmmi(command, build_dir, multicore=None, **kwargs):
                command = list(command)
                if Path(build_dir).name.startswith("zlib-"):
                    command.append("--static")
                else:
                    command.extend([f"--host={triplet}", f"--build={build_triplet}"])
                return original(command, build_dir, multicore, **kwargs)

            cross_cmmi._neu_cross = True
            libraries.cmmi = cross_cmmi
    # pycryptodome's probes compile and link only. cibuildwheel supplies target
    # sysconfig, 64-bit little-endian semantics and NDK CC; no target binary is run.


def _upstream():
    _configure()
    value = json.loads((Path(__file__).parent / ".neu-android-backend.json").read_text())
    module, _, attribute = value["backend"].partition(":")
    backend = importlib.import_module(module)
    return getattr(backend, attribute) if attribute else backend


def get_requires_for_build_wheel(config_settings=None):
    return _upstream().get_requires_for_build_wheel(config_settings)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    return _upstream().prepare_metadata_for_build_wheel(metadata_directory, config_settings)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    return _upstream().build_wheel(wheel_directory, config_settings, metadata_directory)
