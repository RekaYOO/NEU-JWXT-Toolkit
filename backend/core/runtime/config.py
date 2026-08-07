"""Cross-platform runtime configuration.

Development keeps the historical repository-local layout. Packaged desktop and
server builds use writable platform locations and a relocatable resource root.
"""

from __future__ import annotations

import ipaddress
import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable


APP_NAME = "NEU-JWXT-Toolkit"
VALID_PROFILES = {"development", "desktop", "server"}


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    compiled = globals().get("__compiled__")
    if getattr(compiled, "standalone", False):
        return Path(sys.executable).resolve().parent
    return project_root()


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)


def _desktop_root() -> Path:
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def _default_data_dir(profile: str) -> Path:
    override = os.environ.get("NEU_JWXT_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if profile == "desktop":
        return _desktop_root() / "data"
    if profile == "server":
        return Path("/var/lib/neu-jwxt-toolkit")
    return Path.cwd() / "data"


def _default_config_file(profile: str, data_dir: Path) -> Path:
    override = os.environ.get("NEU_JWXT_CONFIG")
    if override:
        return Path(override).expanduser()
    if profile == "server":
        return Path("/etc/neu-jwxt-toolkit/config.json")
    return data_dir / "runtime.json"


def _read_version() -> str:
    override = os.environ.get("NEU_JWXT_VERSION")
    if override:
        return override
    version_file = resource_path("VERSION")
    try:
        return version_file.read_text(encoding="utf-8").strip() or "0.0.0-dev"
    except OSError:
        return "0.0.0-dev"


def secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(stat.S_IRWXU)


def secure_file(path: Path) -> None:
    if os.name != "nt" and path.exists():
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


@dataclass(frozen=True)
class RuntimeConfig:
    profile: str
    version: str
    data_dir: Path
    config_file: Path
    host: str
    port: int
    trusted_proxies: tuple[str, ...] = field(default_factory=tuple)
    access_password_salt: str = ""
    access_password_hash: str = ""
    session_secret: str = ""

    @property
    def access_gateway_enabled(self) -> bool:
        return self.profile == "server"

    @property
    def desktop_mode(self) -> bool:
        return self.profile == "desktop"


def _normalize_profile(raw: str | None) -> str:
    profile = (raw or "development").strip().lower()
    if profile not in VALID_PROFILES:
        raise ValueError(
            f"Invalid NEU_JWXT_PROFILE {profile!r}; expected one of "
            f"{', '.join(sorted(VALID_PROFILES))}"
        )
    return profile


def load_server_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as config_file:
        data = json.load(config_file)
    if not isinstance(data, dict):
        raise ValueError(f"Runtime configuration must be a JSON object: {path}")
    return data


def _trusted_proxies(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return ("127.0.0.1", "::1")
    return tuple(str(item) for item in value)


def is_loopback_host(host: str) -> bool:
    """Whether a bind host is explicitly constrained to this machine."""
    normalized = str(host).strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def get_runtime_config() -> RuntimeConfig:
    profile = _normalize_profile(os.environ.get("NEU_JWXT_PROFILE"))
    data_dir = _default_data_dir(profile)
    config_file = _default_config_file(profile, data_dir)
    file_config = load_server_config(config_file) if profile == "server" else {}
    password_config = file_config.get("access_password") or {}

    default_host = "127.0.0.1"
    default_port = 8000
    host = os.environ.get("HOST", str(file_config.get("host", default_host))).strip()
    port_text = os.environ.get(
        "PORT",
        os.environ.get("BACKEND_PORT", str(file_config.get("port", default_port))),
    )

    if profile == "server" and not is_loopback_host(host):
        raise ValueError(
            "server 模式只允许监听回环地址（127.0.0.1、::1 或 localhost）；"
            "请通过同机 HTTPS 反向代理提供外部访问"
        )

    secure_directory(data_dir)
    return RuntimeConfig(
        profile=profile,
        version=_read_version(),
        data_dir=data_dir,
        config_file=config_file,
        host=host,
        port=int(port_text),
        trusted_proxies=_trusted_proxies(file_config.get("trusted_proxies")),
        access_password_salt=str(password_config.get("salt", "")),
        access_password_hash=str(password_config.get("hash", "")),
        session_secret=str(file_config.get("session_secret", "")),
    )
