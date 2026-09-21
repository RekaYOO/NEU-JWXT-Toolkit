"""Generate the machine-readable manifest consumed by application updaters."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


REPOSITORY = "RekaYOO/NEU-JWXT-Toolkit"
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path, version: str, android_version_code: int) -> dict:
    if not SEMVER.fullmatch(version):
        raise ValueError(f"unsupported release version: {version}")
    if android_version_code < 1:
        raise ValueError("Android version code must be positive")
    names = {
        "linux-server": f"NEU-JWXT-Toolkit-{version}-linux-amd64.tar.gz",
        "windows-desktop": f"NEU-JWXT-Toolkit-{version}-windows-x64-portable.zip",
        "android-client": f"NEU-JWXT-Toolkit-{version}-android-client-arm64.apk",
        "android-local": f"NEU-JWXT-Toolkit-{version}-android-local-arm64.apk",
    }
    assets = {}
    for key, name in names.items():
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f"missing release asset: {name}")
        assets[key] = {
            "name": name,
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
    return {
        "schema_version": 1,
        "repository": REPOSITORY,
        "version": version,
        "tag": f"v{version}",
        "release_url": f"https://github.com/{REPOSITORY}/releases/tag/v{version}",
        "android_version_code": android_version_code,
        "assets": assets,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--android-version-code", required=True, type=int)
    args = parser.parse_args()
    manifest = build_manifest(args.root, args.version, args.android_version_code)
    target = args.root / "release-manifest.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
