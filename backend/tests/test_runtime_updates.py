import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.runtime import updates


def test_semver_comparison_handles_prerelease_and_release():
    assert updates.is_newer("1.2.0", "1.1.9")
    assert updates.is_newer("1.2.0", "1.2.0-rc.1")
    assert not updates.is_newer("1.2.0-rc.1", "1.2.0")
    with pytest.raises(updates.UpdateError):
        updates.is_newer("latest", "1.0.0")


def test_release_manifest_requires_matching_tag_and_declared_assets(monkeypatch):
    names = {
        "linux-server": "NEU-JWXT-Toolkit-2.0.0-linux-amd64.tar.gz",
        "windows-desktop": "NEU-JWXT-Toolkit-2.0.0-windows-x64-portable.zip",
        "android-client": "NEU-JWXT-Toolkit-2.0.0-android-client-arm64.apk",
        "android-local": "NEU-JWXT-Toolkit-2.0.0-android-local-arm64.apk",
    }
    release = {
        "tag_name": "v2.0.0",
        "html_url": "https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/tag/v2.0.0",
        "assets": [
            {"name": "release-manifest.json", "browser_download_url": "https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/download/v2.0.0/release-manifest.json"},
            *[{
                "name": name,
                "browser_download_url": f"https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/download/v2.0.0/{name}",
                "size": 12,
            } for name in names.values()],
        ],
    }
    manifest = {
        "version": "2.0.0",
        "android_version_code": 3,
        "assets": {key: {"name": name, "sha256": "a" * 64, "size": 12} for key, name in names.items()},
    }
    monkeypatch.setattr(updates, "_json", lambda url: release if url == updates.RELEASE_API_URL else manifest)
    result = updates.fetch_latest_manifest()
    assert result.version == "2.0.0"
    assert result.assets["linux-server"].sha256 == "a" * 64


def test_release_manifest_rejects_tag_mismatch(monkeypatch):
    monkeypatch.setattr(updates, "_json", lambda _url: {"tag_name": "v2.0.0", "assets": [
        {"name": "release-manifest.json", "browser_download_url": "https://github.com/x/release-manifest.json"},
    ]} if _url == updates.RELEASE_API_URL else {"version": "1.0.0"})
    with pytest.raises(updates.UpdateError, match="tag"):
        updates.fetch_latest_manifest()


def test_status_reads_root_updater_result(tmp_path):
    config = SimpleNamespace(data_dir=Path(tmp_path), profile="server", version="1.0.0")
    path = Path(tmp_path) / "updates"
    path.mkdir()
    (path / "update-result.env").write_text("job_id=abc\nstate=completed\nversion=2.0.0\n", encoding="utf-8")
    assert updates.UpdateManager().status(config, "abc")["state"] == "completed"
