import hashlib

import pytest

from tools.generate_release_manifest import build_manifest


def test_release_manifest_records_every_platform_asset(tmp_path):
    version = "2.3.4"
    names = (
        f"NEU-JWXT-Toolkit-{version}-linux-amd64.tar.gz",
        f"NEU-JWXT-Toolkit-{version}-windows-x64-portable.zip",
        f"NEU-JWXT-Toolkit-{version}-android-client-arm64.apk",
        f"NEU-JWXT-Toolkit-{version}-android-local-arm64.apk",
    )
    for index, name in enumerate(names):
        (tmp_path / name).write_bytes(f"asset-{index}".encode())

    result = build_manifest(tmp_path, version, 2001003)

    assert result["tag"] == "v2.3.4"
    assert result["android_version_code"] == 2001003
    client = result["assets"]["android-client"]
    assert client["name"] == names[2]
    assert client["sha256"] == hashlib.sha256(b"asset-2").hexdigest()


def test_release_manifest_rejects_missing_assets(tmp_path):
    with pytest.raises(FileNotFoundError, match="linux-amd64"):
        build_manifest(tmp_path, "2.3.4", 1)


def test_release_manifest_rejects_invalid_versions(tmp_path):
    with pytest.raises(ValueError, match="unsupported"):
        build_manifest(tmp_path, "latest", 1)
