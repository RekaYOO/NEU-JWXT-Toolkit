"""Release discovery and staged application updates.

The updater deliberately keeps GitHub access and filesystem staging separate
from the web UI.  Only assets named by the official release manifest can be
downloaded; callers never provide an arbitrary URL or command.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .config import RuntimeConfig


REPOSITORY = "RekaYOO/NEU-JWXT-Toolkit"
RELEASE_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASE_PAGE_URL = f"https://github.com/{REPOSITORY}/releases/latest"
MANIFEST_ASSET = "release-manifest.json"
MAX_RELEASE_BYTES = 1024 * 1024 * 1024
METADATA_TTL_SECONDS = 600

_SEMVER = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


class UpdateError(RuntimeError):
    """A safe, user-facing update failure."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    tag: str
    release_url: str
    android_version_code: int
    assets: dict[str, ReleaseAsset]


def _version_key(value: str) -> tuple[Any, ...]:
    match = _SEMVER.fullmatch(str(value).strip().lstrip("v"))
    if not match:
        raise UpdateError(f"无法识别发行版本: {value}")
    pre = match.group("pre")
    if pre is None:
        pre_key: tuple[Any, ...] = (1,)
    else:
        parts: list[tuple[int, Any]] = []
        for item in pre.split("."):
            parts.append((0, int(item)) if item.isdigit() else (1, item))
        pre_key = (0, tuple(parts))
    return int(match.group("major")), int(match.group("minor")), int(match.group("patch")), pre_key


def is_newer(remote: str, current: str) -> bool:
    return _version_key(remote) > _version_key(current)


def _request_bytes(url: str, *, timeout: float = 12.0, max_bytes: int = MAX_RELEASE_BYTES) -> bytes:
    if not _allowed_release_url(url):
        raise UpdateError("不允许的更新地址")
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "NEU-JWXT-Toolkit-update-checker",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UpdateError("发行包超过允许的大小")
                chunks.append(chunk)
            return b"".join(chunks)
    except UpdateError:
        raise
    except (OSError, HTTPError, URLError, TimeoutError) as error:
        raise UpdateError("无法连接 GitHub Release") from error


def _json(url: str) -> dict[str, Any]:
    try:
        value = json.loads(_request_bytes(url, max_bytes=4 * 1024 * 1024).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpdateError("GitHub Release 元数据格式无效") from error
    if not isinstance(value, dict):
        raise UpdateError("GitHub Release 元数据格式无效")
    return value


def _allowed_release_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and (
        parsed.hostname in {"api.github.com", "github.com", "release-assets.githubusercontent.com"}
        or bool(parsed.hostname and parsed.hostname.endswith(".githubusercontent.com"))
    )


def _asset_lookup(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in release.get("assets", ()):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        url = str(item.get("browser_download_url") or "")
        if name and url.startswith("https://github.com/"):
            result[name] = item
    return result


def fetch_latest_manifest() -> ReleaseManifest:
    release = _json(RELEASE_API_URL)
    tag = str(release.get("tag_name") or "")
    release_url = str(release.get("html_url") or RELEASE_PAGE_URL)
    assets = _asset_lookup(release)
    manifest_asset = assets.get(MANIFEST_ASSET)
    if not manifest_asset:
        raise UpdateError("当前 Release 缺少版本清单")
    manifest = _json(str(manifest_asset["browser_download_url"]))
    version = str(manifest.get("version") or "").strip()
    if not version or tag != f"v{version}":
        raise UpdateError("Release 版本清单与 tag 不一致")
    _version_key(version)
    try:
        android_code = int(manifest.get("android_version_code"))
    except (TypeError, ValueError) as error:
        raise UpdateError("Android 版本号无效") from error
    if android_code < 1:
        raise UpdateError("Android 版本号无效")
    raw_assets = manifest.get("assets")
    if not isinstance(raw_assets, dict):
        raise UpdateError("Release 资产清单无效")
    parsed: dict[str, ReleaseAsset] = {}
    for key in ("linux-server", "windows-desktop", "android-client", "android-local"):
        value = raw_assets.get(key)
        if not isinstance(value, dict):
            raise UpdateError(f"Release 资产清单缺少: {key}")
        name = str(value.get("name") or "")
        remote = assets.get(name)
        url = str(remote.get("browser_download_url") if remote else "")
        digest = str(value.get("sha256") or "").lower()
        if (
            "/" in name
            or "\\" in name
            or not url
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise UpdateError(f"Release 资产清单无效: {key}")
        try:
            size = int(value.get("size") or remote.get("size") or 0)
        except (TypeError, ValueError) as error:
            raise UpdateError(f"Release 资产大小无效: {key}") from error
        if size < 1:
            raise UpdateError(f"Release 资产大小无效: {key}")
        remote_size = remote.get("size")
        if remote_size not in (None, ""):
            try:
                if int(remote_size) != size:
                    raise UpdateError(f"Release 资产大小不一致: {key}")
            except (TypeError, ValueError) as error:
                raise UpdateError(f"Release 资产大小无效: {key}") from error
        parsed[key] = ReleaseAsset(name, url, size, digest)
    if not parsed:
        raise UpdateError("Release 没有可用资产")
    return ReleaseManifest(version, tag, release_url, android_code, parsed)


class UpdateManager:
    """Process-local metadata cache and one-at-a-time Linux staging job."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._manifest: ReleaseManifest | None = None
        self._manifest_at = 0.0
        self._jobs: dict[str, dict[str, Any]] = {}

    def manifest(self, *, force: bool = False) -> ReleaseManifest:
        with self._lock:
            if not force and self._manifest and time.monotonic() - self._manifest_at < METADATA_TTL_SECONDS:
                return self._manifest
        value = fetch_latest_manifest()
        with self._lock:
            self._manifest = value
            self._manifest_at = time.monotonic()
        return value

    def snapshot(self, config: RuntimeConfig, *, force: bool = False) -> dict[str, Any]:
        current = config.version
        try:
            latest = self.manifest(force=force)
            newer = is_newer(latest.version, current)
            error = None
        except UpdateError as exception:
            latest = None
            newer = False
            error = str(exception)
        capability = "none"
        asset_key = None
        if config.profile == "server":
            capability = "linux-auto" if self.auto_linux_available(config) else "linux-manual"
            asset_key = "linux-server"
        elif config.profile == "desktop":
            capability, asset_key = "windows-download", "windows-desktop"
        result: dict[str, Any] = {
            "current_version": current,
            "profile": config.profile,
            "capability": capability,
            "latest_version": latest.version if latest else None,
            "android_version_code": latest.android_version_code if latest else None,
            "update_available": newer,
            "release_url": latest.release_url if latest else RELEASE_PAGE_URL,
            "asset": None,
            "error": error,
        }
        if latest and asset_key in latest.assets:
            asset = latest.assets[asset_key]
            result["asset"] = {"name": asset.name, "size": asset.size, "sha256": asset.sha256}
        return result

    def _status_file(self, config: RuntimeConfig) -> Path:
        return config.data_dir / "updates" / "update-status.json"

    @staticmethod
    def auto_linux_available(config: RuntimeConfig) -> bool:
        return config.profile == "server" and Path(
            "/etc/systemd/system/neu-jwxt-toolkit-updater.path"
        ).is_file()

    def _write_status(self, config: RuntimeConfig, status: dict[str, Any]) -> None:
        target = self._status_file(config)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)

    def status(self, config: RuntimeConfig, job_id: str | None = None) -> dict[str, Any] | None:
        result_path = config.data_dir / "updates" / "update-result.env"
        try:
            result_lines = result_path.read_text(encoding="utf-8").splitlines()
            result = dict(line.split("=", 1) for line in result_lines if "=" in line)
            if result.get("job_id") and (not job_id or result.get("job_id") == job_id):
                return result
        except (OSError, ValueError):
            pass
        with self._lock:
            if job_id and job_id in self._jobs:
                return dict(self._jobs[job_id])
        path = self._status_file(config)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def start_download(self, config: RuntimeConfig) -> dict[str, Any]:
        if config.profile not in {"server", "desktop"}:
            raise UpdateError("当前运行模式不支持应用更新下载")
        manifest = self.manifest()
        if not is_newer(manifest.version, config.version):
            raise UpdateError("当前已经是最新版本")
        asset_key = "linux-server" if config.profile == "server" else "windows-desktop"
        asset = manifest.assets.get(asset_key)
        if not asset:
            raise UpdateError("当前 Release 缺少对应平台资产")
        with self._lock:
            active = next((item for item in self._jobs.values() if item.get("state") in {"downloading", "staged", "requested", "downloaded"}), None)
            if active:
                return dict(active)
            job_id = uuid.uuid4().hex
            state = {"job_id": job_id, "state": "downloading", "version": manifest.version, "asset": asset.name}
            self._jobs[job_id] = state
        self._write_status(config, state)
        threading.Thread(target=self._download_worker, args=(config, manifest, asset, job_id), daemon=True, name="release-update").start()
        return dict(state)

    start_linux_download = start_download

    def _download_worker(self, config: RuntimeConfig, manifest: ReleaseManifest, asset: ReleaseAsset, job_id: str) -> None:
        root = config.data_dir / "updates" / manifest.version
        temporary = root / f"{asset.name}.part"
        final = root / asset.name
        state = {"job_id": job_id, "state": "downloading", "version": manifest.version, "asset": asset.name}
        try:
            root.mkdir(parents=True, exist_ok=True)
            request = Request(asset.url, headers={"User-Agent": "NEU-JWXT-Toolkit-updater", "Accept": "application/octet-stream"})
            digest = hashlib.sha256()
            size = 0
            with urlopen(request, timeout=30) as response, temporary.open("wb") as output:
                if not _allowed_release_url(response.geturl()):
                    raise UpdateError("下载地址跳转到不受信任的域名")
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_RELEASE_BYTES:
                        raise UpdateError("发行包超过允许的大小")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
            if digest.hexdigest().lower() != asset.sha256:
                raise UpdateError("发行包校验失败")
            temporary.replace(final)
            state.update({"state": "staged", "path": str(final), "sha256": asset.sha256, "size": size})
            if config.profile == "server" and self.auto_linux_available(config):
                request_path = config.data_dir / "updates" / "request.env"
                request_path.write_text(
                    f"job_id={job_id}\nversion={manifest.version}\nasset={asset.name}\nsha256={asset.sha256}\n",
                    encoding="ascii",
                )
                request_path.chmod(0o600)
                state["state"] = "requested"
            else:
                state["state"] = "downloaded"
        except Exception as error:
            temporary.unlink(missing_ok=True)
            state.update({"state": "failed", "error": str(error)})
        with self._lock:
            self._jobs[job_id] = state
        self._write_status(config, state)


manager = UpdateManager()
