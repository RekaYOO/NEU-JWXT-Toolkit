"""Prepare disposable upstream sdists without editing repository dependencies."""

from __future__ import annotations

import argparse
import json
import hashlib
from pathlib import Path
import shutil
import tarfile
import urllib.request

import tomlkit


def prepare(archives: Path, destination: Path) -> None:
    locks = json.loads(Path(__file__).with_name("sources.json").read_text())
    for lock in locks:
        archive = archives / lock["filename"]
        if not archive.exists():
            urllib.request.urlretrieve(lock["url"], archive)
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != lock["sha256"]:
            raise ValueError(f"Source archive digest mismatch: {archive.name}")
    for archive in sorted(archives.glob("*.tar.gz")):
        with tarfile.open(archive) as source:
            source.extractall(destination, filter="data")
    for source in sorted(destination.iterdir()):
        if not source.is_dir():
            continue
        path = source / "pyproject.toml"
        config = tomlkit.parse(path.read_text()) if path.exists() else tomlkit.document()
        if "build-system" not in config:
            config["build-system"] = {"requires": ["setuptools", "wheel"], "build-backend": "setuptools.build_meta"}
        system = config["build-system"]
        backend = system.get("build-backend", "setuptools.build_meta:__legacy__")
        (source / ".neu-android-backend.json").write_text(json.dumps({"backend": backend}), encoding="utf-8")
        system["build-backend"] = "neu_android_backend"
        system["backend-path"] = [".", *[value for value in system.get("backend-path", []) if value != "."]]
        path.write_text(tomlkit.dumps(config), encoding="utf-8")
        shutil.copyfile(Path(__file__).with_name("neu_android_backend.py"), source / "neu_android_backend.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archives", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    prepare(args.archives, args.destination)
