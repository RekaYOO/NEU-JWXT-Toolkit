import json
import hashlib
import io
import os
from urllib.error import HTTPError, URLError
from datetime import datetime, timedelta, timezone
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


def test_status_keeps_jobs_separate_and_preserves_root_install_stages(tmp_path):
    config = SimpleNamespace(data_dir=tmp_path, profile="server", version="1.0.0")
    manager = updates.UpdateManager()
    manager._write_status(config, {"job_id": "a" * 32, "state": "requested", "version": "2.0.0"})
    result = tmp_path / "updates" / "update-result.env"
    result.write_text(
        "job_id=" + "a" * 32 + "\nstate=completed\nversion=2.0.0\n"
        "install_started_at=2026-09-28T10:00:00Z\nupdated_at=2026-09-28T10:01:00Z\n",
        encoding="utf-8",
    )
    complete = manager.status(config)
    assert complete["state"] == "completed"
    assert updates.UpdateManager().status(config, "a" * 32)["state"] == "completed"
    assert [entry["state"] for entry in complete["history"]] == [
        "requested", "installing", "completed",
    ]
    assert manager.status(config, "b" * 32) is None

    manager._write_status(config, {"job_id": "b" * 32, "state": "downloading", "version": "3.0.0"}, new_job=True)
    assert manager.status(config)["job_id"] == "b" * 32
    assert manager.status(config)["state"] == "downloading"


def test_root_updater_failure_is_visible_after_server_restart(tmp_path):
    config = SimpleNamespace(data_dir=tmp_path, profile="server", version="1.0.0")
    manager = updates.UpdateManager()
    manager._write_status(config, {"job_id": "a" * 32, "state": "requested", "version": "2.0.0"})
    (tmp_path / "updates" / "update-result.env").write_text(
        "job_id=" + "a" * 32 + "\nstate=failed\nversion=2.0.0\n"
        "error=更新包 SHA-256 校验失败\nupdated_at=2026-09-28T10:01:00Z\n",
        encoding="utf-8",
    )
    result = updates.UpdateManager().status(config)
    assert result["state"] == "failed"
    assert result["error"] == "更新包 SHA-256 校验失败"
    assert result["history"][-1]["state"] == "failed"


def test_long_install_warns_without_launching_a_competing_update(tmp_path):
    config = SimpleNamespace(data_dir=tmp_path, profile="server", version="1.0.0")
    manager = updates.UpdateManager()
    manager._write_status(config, {"job_id": "a" * 32, "state": "requested", "version": "2.0.0"})
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=25)).isoformat()
    (tmp_path / "updates" / "update-result.env").write_text(
        f"job_id={'a' * 32}\nstate=installing\nversion=2.0.0\n"
        f"install_started_at={old_time}\nupdated_at={old_time}\n",
        encoding="utf-8",
    )
    result = manager.status(config)
    assert result["state"] == "installing"
    assert "超过预期时间" in result["error"]


def test_active_job_is_reused_after_server_restart_without_another_download(tmp_path, monkeypatch):
    config = SimpleNamespace(data_dir=tmp_path, profile="server", version="1.0.0")
    updates.UpdateManager()._write_status(config, {
        "job_id": "a" * 32, "state": "requested", "version": "2.0.0",
    })
    manager = updates.UpdateManager()
    asset = updates.ReleaseAsset("package.tar.gz", "https://github.com/example", 1, "a" * 64)
    monkeypatch.setattr(manager, "manifest", lambda **_kwargs: updates.ReleaseManifest(
        "2.0.0", "v2.0.0", "https://github.com/", 1, {"linux-server": asset},
    ))
    def unexpected_thread(**_kwargs):
        raise AssertionError("existing job must not launch a second download")
    monkeypatch.setattr(updates.threading, "Thread", unexpected_thread)
    assert manager.start_download(config)["job_id"] == "a" * 32


def test_stalled_request_becomes_a_visible_failure_and_allows_retry(tmp_path, monkeypatch):
    config = SimpleNamespace(data_dir=tmp_path, profile="server", version="1.0.0")
    manager = updates.UpdateManager()
    old = "a" * 32
    manager._write_status(config, {"job_id": old, "state": "requested", "version": "2.0.0"})
    path = tmp_path / "updates" / "update-status.json"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    persisted["updated_at"] = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    path.write_text(json.dumps(persisted), encoding="utf-8")
    failure = manager.status(config)
    assert failure["state"] == "failed"
    assert "更新器未接管" in failure["error"]
    assert failure["history"][-1]["state"] == "failed"

    asset = updates.ReleaseAsset("package.tar.gz", "https://github.com/example", 1, "a" * 64)
    monkeypatch.setattr(manager, "manifest", lambda **_kwargs: updates.ReleaseManifest(
        "2.0.0", "v2.0.0", "https://github.com/", 1, {"linux-server": asset},
    ))
    class FakeThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(updates.threading, "Thread", FakeThread)
    retried = manager.start_download(config)
    assert retried["job_id"] != old
    assert retried["state"] == "downloading"


def test_legacy_stalled_request_without_timestamp_can_be_retried(tmp_path):
    config = SimpleNamespace(data_dir=tmp_path, profile="server", version="1.0.0")
    path = tmp_path / "updates"
    path.mkdir()
    status_file = path / "update-status.json"
    status_file.write_text(json.dumps({
        "job_id": "a" * 32, "state": "requested", "version": "2.0.0",
    }), encoding="utf-8")
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=3)).timestamp()
    os.utime(status_file, (old_time, old_time))
    result = updates.UpdateManager().status(config)
    assert result["state"] == "failed"
    assert "更新器未接管" in result["error"]


def test_download_worker_records_progress_and_waiting_for_linux_updater(tmp_path, monkeypatch):
    content = b"safe test archive"
    digest = hashlib.sha256(content).hexdigest()
    asset = updates.ReleaseAsset("package.tar.gz", "https://github.com/example", len(content), digest)
    manifest = updates.ReleaseManifest("2.0.0", "v2.0.0", "https://github.com/", 1, {"linux-server": asset})
    config = SimpleNamespace(data_dir=tmp_path, profile="server", version="1.0.0")
    manager = updates.UpdateManager()

    class FakeResponse(io.BytesIO):
        def geturl(self):
            return asset.url

    monkeypatch.setattr(updates, "urlopen", lambda *_args, **_kwargs: FakeResponse(content))
    monkeypatch.setattr(manager, "auto_linux_available", lambda _config: True)
    manager._write_status(config, {"job_id": "a" * 32, "state": "downloading", "version": "2.0.0"})
    manager._download_worker(config, manifest, asset, "a" * 32)
    status = manager.status(config)
    assert status["state"] == "requested"
    assert status["bytes_downloaded"] == len(content)
    assert [entry["state"] for entry in status["history"]] == [
        "downloading", "verifying", "staged", "requested",
    ]
    request_path = tmp_path / "updates" / "request.env"
    assert f"job_id={'a' * 32}" in request_path.read_text(encoding="ascii")
    if os.name != "nt":
        assert request_path.stat().st_mode & 0o777 == 0o600


def test_request_is_present_before_waiting_state_is_persisted(tmp_path, monkeypatch):
    content = b"safe test archive"
    asset = updates.ReleaseAsset("package.tar.gz", "https://github.com/example", len(content), hashlib.sha256(content).hexdigest())
    manifest = updates.ReleaseManifest("2.0.0", "v2.0.0", "https://github.com/", 1, {"linux-server": asset})
    config = SimpleNamespace(data_dir=tmp_path, profile="server", version="1.0.0")
    manager = updates.UpdateManager()

    class FakeResponse(io.BytesIO):
        def geturl(self):
            return asset.url

    monkeypatch.setattr(updates, "urlopen", lambda *_args, **_kwargs: FakeResponse(content))
    monkeypatch.setattr(manager, "auto_linux_available", lambda _config: True)
    original_write = manager._write_status
    observed = []

    def observe_status(cfg, state, **kwargs):
        if state.get("state") == "requested":
            observed.append((cfg.data_dir / "updates" / "request.env").exists())
        return original_write(cfg, state, **kwargs)

    monkeypatch.setattr(manager, "_write_status", observe_status)
    manager._write_status(config, {"job_id": "a" * 32, "state": "downloading", "version": "2.0.0"})
    manager._download_worker(config, manifest, asset, "a" * 32)
    assert observed == [True]


def test_download_retries_a_transient_failure_then_submits_once(tmp_path, monkeypatch):
    content = b"verified release"
    asset = updates.ReleaseAsset(
        "package.tar.gz", "https://github.com/example", len(content), hashlib.sha256(content).hexdigest(),
    )
    manifest = updates.ReleaseManifest("2.0.0", "v2.0.0", "https://github.com/", 1, {"linux-server": asset})
    config = SimpleNamespace(data_dir=tmp_path, profile="server", version="1.0.0")
    manager = updates.UpdateManager()
    manager._write_status(config, {"job_id": "a" * 32, "state": "downloading", "version": "2.0.0"})
    calls = []

    class FakeResponse(io.BytesIO):
        def geturl(self):
            return asset.url

    def flaky_urlopen(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise URLError("temporary connection loss")
        return FakeResponse(content)

    monkeypatch.setattr(updates, "urlopen", flaky_urlopen)
    monkeypatch.setattr(updates.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(manager, "auto_linux_available", lambda _config: True)
    manager._download_worker(config, manifest, asset, "a" * 32)
    assert len(calls) == 2
    assert manager.status(config)["state"] == "requested"
    assert manager.status(config)["attempt"] == 2
    assert (tmp_path / "updates" / "request.env").read_text(encoding="ascii").count("job_id=") == 1
    assert not list((tmp_path / "updates" / "2.0.0").glob("*.part"))


def test_download_retries_incomplete_content_then_fails_without_request(tmp_path, monkeypatch):
    content = b"complete release"
    asset = updates.ReleaseAsset(
        "package.tar.gz", "https://github.com/example", len(content), hashlib.sha256(content).hexdigest(),
    )
    manifest = updates.ReleaseManifest("2.0.0", "v2.0.0", "https://github.com/", 1, {"linux-server": asset})
    config = SimpleNamespace(data_dir=tmp_path, profile="server", version="1.0.0")
    manager = updates.UpdateManager()
    manager._write_status(config, {"job_id": "a" * 32, "state": "downloading", "version": "2.0.0"})
    calls = []

    class FakeResponse(io.BytesIO):
        def geturl(self):
            return asset.url

    def incomplete_urlopen(*_args, **_kwargs):
        calls.append(1)
        return FakeResponse(content[:4])

    monkeypatch.setattr(updates, "urlopen", incomplete_urlopen)
    monkeypatch.setattr(updates.time, "sleep", lambda _seconds: None)
    manager._download_worker(config, manifest, asset, "a" * 32)
    status = manager.status(config)
    assert len(calls) == updates.DOWNLOAD_ATTEMPTS
    assert status["state"] == "failed"
    assert "已尝试 3 次" in status["error"]
    assert status["message"] == updates.STATE_MESSAGES["failed"]
    assert not (tmp_path / "updates" / "request.env").exists()
    assert not list((tmp_path / "updates" / "2.0.0").glob("*.part"))


def test_download_does_not_retry_permanent_http_error(tmp_path, monkeypatch):
    asset = updates.ReleaseAsset("package.tar.gz", "https://github.com/example", 4, "a" * 64)
    manifest = updates.ReleaseManifest("2.0.0", "v2.0.0", "https://github.com/", 1, {"linux-server": asset})
    config = SimpleNamespace(data_dir=tmp_path, profile="server", version="1.0.0")
    manager = updates.UpdateManager()
    manager._write_status(config, {"job_id": "a" * 32, "state": "downloading", "version": "2.0.0"})
    calls = []

    def missing_urlopen(*_args, **_kwargs):
        calls.append(1)
        raise HTTPError(asset.url, 404, "Not Found", {}, None)

    monkeypatch.setattr(updates, "urlopen", missing_urlopen)
    manager._download_worker(config, manifest, asset, "a" * 32)
    assert len(calls) == 1
    assert "HTTP 404" in manager.status(config)["error"]


def test_linux_watcher_recovers_pending_request_without_replaying_claimed_one():
    root = Path(__file__).resolve().parents[2] / "packaging" / "linux"
    watcher = (root / "neu-jwxt-toolkit-updater.path").read_text(encoding="utf-8")
    helper = (root / "update-helper.sh").read_text(encoding="utf-8")
    assert "PathExists=/var/lib/neu-jwxt-toolkit/updates/request.env" in watcher
    assert 'mv -T -- "${REQUEST_FILE}" "${claimed_request}"' in helper
    assert 'rm -f -- "${REQUEST_FILE}"' not in helper.split('claimed_request=', 1)[1]


def test_linux_auto_capability_requires_an_active_path_unit(monkeypatch):
    config = SimpleNamespace(profile="server")
    monkeypatch.setattr(Path, "is_file", lambda _self: True)
    monkeypatch.setattr(updates.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=3))
    assert not updates.UpdateManager.auto_linux_available(config)
    monkeypatch.setattr(updates.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    assert updates.UpdateManager.auto_linux_available(config)


def test_late_worker_cannot_replace_a_newer_update_attempt(tmp_path, monkeypatch):
    config = SimpleNamespace(data_dir=tmp_path, profile="server", version="1.0.0")
    manager = updates.UpdateManager()
    old, current = "a" * 32, "b" * 32
    manager._write_status(config, {"job_id": old, "state": "failed", "version": "2.0.0"})
    manager._write_status(config, {"job_id": current, "state": "downloading", "version": "2.0.0"}, new_job=True)
    content = b"old download finished late"
    asset = updates.ReleaseAsset("package.tar.gz", "https://github.com/example", len(content), hashlib.sha256(content).hexdigest())
    manifest = updates.ReleaseManifest("2.0.0", "v2.0.0", "https://github.com/", 1, {"linux-server": asset})

    class FakeResponse(io.BytesIO):
        def geturl(self):
            return asset.url

    monkeypatch.setattr(updates, "urlopen", lambda *_args, **_kwargs: FakeResponse(content))
    monkeypatch.setattr(manager, "auto_linux_available", lambda _config: True)
    manager._download_worker(config, manifest, asset, old)
    assert manager.status(config)["job_id"] == current
    assert manager.status(config)["state"] == "downloading"
    assert not (tmp_path / "updates" / "request.env").exists()
