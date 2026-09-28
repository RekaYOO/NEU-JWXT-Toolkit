"""Release discovery and staged application updates.

The updater deliberately keeps GitHub access and filesystem staging separate
from the web UI.  Only assets named by the official release manifest can be
downloaded; callers never provide an arbitrary URL or command.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
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
REQUEST_TIMEOUT_SECONDS = 120
DOWNLOAD_TIMEOUT_SECONDS = 180
INSTALL_TIMEOUT_SECONDS = 1200
DOWNLOAD_ATTEMPTS = 3

STATE_MESSAGES = {
    "downloading": "正在下载更新包",
    "verifying": "正在校验更新包",
    "staged": "更新包已准备",
    "requested": "等待服务端更新器接管",
    "installing": "正在安装并检查新版本",
    "downloaded": "更新包已下载，需要手动安装",
    "completed": "更新已完成",
    "failed": "更新失败",
}

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
        if config.profile != "server" or not Path(
            "/etc/systemd/system/neu-jwxt-toolkit-updater.path"
        ).is_file():
            return False
        try:
            return subprocess.run(
                ["systemctl", "is-active", "--quiet", "neu-jwxt-toolkit-updater.path"],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=2,
            ).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _read_status(self, config: RuntimeConfig) -> dict[str, Any] | None:
        try:
            value = json.loads(self._status_file(config).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) and value.get("job_id") else None

    def _read_result(self, config: RuntimeConfig) -> dict[str, str] | None:
        try:
            lines = (config.data_dir / "updates" / "update-result.env").read_text(encoding="utf-8").splitlines()
            value = dict(line.split("=", 1) for line in lines if "=" in line)
        except (OSError, ValueError):
            return None
        if not re.fullmatch(r"[0-9a-f]{1,64}", value.get("job_id", "")):
            return None
        return value if value.get("state") in {"installing", "completed", "failed"} else None

    def _write_status(self, config: RuntimeConfig, status: dict[str, Any], *, new_job: bool = False) -> None:
        with self._lock:
            previous = self._read_status(config)
            if previous and previous.get("job_id") != status.get("job_id") and not new_job:
                return
            same_job = previous and previous.get("job_id") == status.get("job_id")
            history = list(previous.get("history", [])) if same_job else []
            now = self._now()
            value = {
                **status,
                "updated_at": now,
                "message": status.get("message") or STATE_MESSAGES.get(status.get("state"), ""),
            }
            if not history or history[-1]["state"] != value["state"]:
                history.append({"state": value["state"], "at": now, "message": value["message"]})
            value["history"] = history[-16:]
            target = self._status_file(config)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)
            self._jobs[value["job_id"]] = value

    def _ensure_current_job(self, config: RuntimeConfig, job_id: str) -> None:
        with self._lock:
            if (self._read_status(config) or {}).get("job_id") != job_id:
                raise UpdateError("更新任务已由新的尝试替代")

    def status(self, config: RuntimeConfig, job_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            current = self._read_status(config)
            value = current if current and (not job_id or current.get("job_id") == job_id) else None
            if value is None and job_id:
                value = self._jobs.get(job_id)
            result = self._read_result(config)
            if value is None and result and (not job_id or result["job_id"] == job_id) and current is None:
                value = result
            if value is None:
                return None
            value = dict(value)
            if result and result["job_id"] == value["job_id"]:
                history = list(value.get("history", []))
                for stage, timestamp in (
                    ("installing", result.get("install_started_at") or result.get("updated_at")),
                    (result["state"], result.get("updated_at")),
                ):
                    if stage == "installing" and not result.get("install_started_at") and result["state"] != "installing":
                        continue
                    if timestamp and not any(item.get("state") == stage for item in history):
                        history.append({"state": stage, "at": timestamp, "message": STATE_MESSAGES[stage]})
                value.update(result)
                value["history"] = history[-16:]
                value["message"] = STATE_MESSAGES[result["state"]]
                if result["state"] == "installing" and result.get("updated_at"):
                    try:
                        install_age = (datetime.now(timezone.utc) - datetime.fromisoformat(
                            result["updated_at"].replace("Z", "+00:00")
                        )).total_seconds()
                    except ValueError:
                        install_age = 0
                    if install_age > INSTALL_TIMEOUT_SECONDS:
                        value["error"] = "安装超过预期时间，请检查服务端更新器日志；安装可能仍在进行"
                return value
            limits = {
                "downloading": DOWNLOAD_TIMEOUT_SECONDS,
                "verifying": DOWNLOAD_TIMEOUT_SECONDS,
                "staged": REQUEST_TIMEOUT_SECONDS,
                "requested": REQUEST_TIMEOUT_SECONDS,
                "installing": INSTALL_TIMEOUT_SECONDS,
            }
            timeout = limits.get(value.get("state"))
            if timeout:
                try:
                    if value.get("updated_at"):
                        age = (datetime.now(timezone.utc) - datetime.fromisoformat(
                            value["updated_at"].replace("Z", "+00:00")
                        )).total_seconds()
                    else:
                        age = time.time() - self._status_file(config).stat().st_mtime
                except (OSError, TypeError, ValueError):
                    age = 0
                if age > timeout:
                    error = {
                        "requested": "更新器未接管任务，请检查 systemd 更新器状态后重试",
                        "installing": "安装长时间没有回执，请检查服务端更新器日志",
                    }.get(value["state"], "更新任务中断或超时，请重试")
                    value.update(state="failed", error=error, message=STATE_MESSAGES["failed"])
                    self._write_status(config, value)
                    return dict(self._jobs[value["job_id"]])
            return value

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
            active = self.status(config)
            if active and active.get("state") in {"downloading", "verifying", "staged", "requested", "installing"}:
                if active.get("version") != manifest.version:
                    raise UpdateError("已有其他版本的更新任务正在进行")
                return active
            job_id = uuid.uuid4().hex
            state = {
                "job_id": job_id, "state": "downloading", "version": manifest.version,
                "asset": asset.name, "bytes_downloaded": 0, "bytes_total": asset.size,
            }
            self._write_status(config, state, new_job=True)
        threading.Thread(target=self._download_worker, args=(config, manifest, asset, job_id), daemon=True, name="release-update").start()
        return self.status(config, job_id) or state

    start_linux_download = start_download

    def _download_worker(self, config: RuntimeConfig, manifest: ReleaseManifest, asset: ReleaseAsset, job_id: str) -> None:
        root = config.data_dir / "updates" / manifest.version
        temporary = root / f"{asset.name}.{job_id}.part"
        final = root / asset.name
        state = {
            "job_id": job_id, "state": "downloading", "version": manifest.version,
            "asset": asset.name, "bytes_downloaded": 0, "bytes_total": asset.size,
        }
        stage = "下载"
        try:
            root.mkdir(parents=True, exist_ok=True)
            request = Request(asset.url, headers={"User-Agent": "NEU-JWXT-Toolkit-updater", "Accept": "application/octet-stream"})
            for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
                self._ensure_current_job(config, job_id)
                state.update(
                    bytes_downloaded=0, attempt=attempt, attempts=DOWNLOAD_ATTEMPTS,
                    message=f"正在下载更新包（第 {attempt}/{DOWNLOAD_ATTEMPTS} 次）",
                )
                self._write_status(config, state)
                digest = hashlib.sha256()
                size = 0
                last_report = time.monotonic()
                try:
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
                            if time.monotonic() - last_report >= 1:
                                state["bytes_downloaded"] = size
                                self._write_status(config, state)
                                last_report = time.monotonic()
                        output.flush()
                    if size != asset.size or digest.hexdigest().lower() != asset.sha256:
                        raise UpdateError("下载内容与发行清单不一致")
                    break
                except HTTPError as error:
                    if error.code not in {408, 429} and error.code < 500:
                        raise UpdateError(f"下载资源不可用（HTTP {error.code}）") from error
                    reason = f"下载服务暂时不可用（HTTP {error.code}）"
                except (URLError, TimeoutError, OSError) as error:
                    reason = "网络中断或连接超时"
                except UpdateError as error:
                    if str(error) != "下载内容与发行清单不一致":
                        raise
                    reason = str(error)
                temporary.unlink(missing_ok=True)
                if attempt == DOWNLOAD_ATTEMPTS:
                    raise UpdateError(f"{reason}，已尝试 {DOWNLOAD_ATTEMPTS} 次")
                state["message"] = f"{reason}，准备第 {attempt + 1}/{DOWNLOAD_ATTEMPTS} 次下载"
                self._write_status(config, state)
                time.sleep(attempt)
            stage = "校验"
            state.pop("message", None)
            state.update(state="verifying", bytes_downloaded=size)
            self._write_status(config, state)
            with self._lock:
                self._ensure_current_job(config, job_id)
                temporary.replace(final)
                state.update({"state": "staged", "path": str(final), "sha256": asset.sha256, "size": size})
                self._write_status(config, state)
            if config.profile == "server" and self.auto_linux_available(config):
                stage = "提交更新请求"
                with self._lock:
                    self._ensure_current_job(config, job_id)
                    request_path = config.data_dir / "updates" / "request.env"
                    request_temp = request_path.with_suffix(".tmp")
                    request_temp.write_text(
                        f"job_id={job_id}\nversion={manifest.version}\nasset={asset.name}\nsha256={asset.sha256}\n",
                        encoding="ascii",
                    )
                    request_temp.chmod(0o600)
                    request_temp.replace(request_path)
                    state["state"] = "requested"
                    self._write_status(config, state)
            else:
                state["state"] = "downloaded"
                self._write_status(config, state)
        except Exception as error:
            temporary.unlink(missing_ok=True)
            state.pop("message", None)
            state.update({"state": "failed", "error": f"{stage}失败：{error}"})
            self._write_status(config, state)


manager = UpdateManager()
