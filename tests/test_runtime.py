import importlib
import json
import os
import asyncio
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from backend.core.runtime.access import (
    COOKIE_TTL_SECONDS,
    LoginRateLimiter,
    hash_access_password,
    issue_access_cookie,
    validate_access_cookie,
    verify_access_password,
)


def test_password_hash_round_trip():
    password_data = hash_access_password("a-secure-password")
    assert verify_access_password(
        "a-secure-password",
        password_data["salt"],
        password_data["hash"],
    )
    assert not verify_access_password(
        "wrong-password",
        password_data["salt"],
        password_data["hash"],
    )
    with pytest.raises(ValueError, match="不能超过"):
        hash_access_password("x" * 257)


def test_signed_cookie_rejects_tampering_and_expiry():
    token = issue_access_cookie("test-secret", now=1_000)
    assert validate_access_cookie(token, "test-secret", now=1_001)
    assert not validate_access_cookie(token + "x", "test-secret", now=1_001)
    assert not validate_access_cookie(
        token,
        "test-secret",
        now=1_000 + COOKIE_TTL_SECONDS + 1,
    )
    assert not validate_access_cookie("x" * 2049, "test-secret", now=1_001)


def test_rate_limiter_blocks_fifth_failure():
    limiter = LoginRateLimiter()
    for offset in range(5):
        limiter.register_failure("client", now=100 + offset)
    assert limiter.is_blocked("client", now=105)
    assert not limiter.is_blocked("client", now=100 + 301)


def test_runtime_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("NEU_JWXT_PROFILE", "desktop")
    monkeypatch.setenv("NEU_JWXT_DATA_DIR", str(tmp_path / "custom-data"))
    from backend.core.runtime import config as runtime_config

    config = runtime_config.get_runtime_config()
    assert config.profile == "desktop"
    assert config.data_dir == tmp_path / "custom-data"
    assert config.data_dir.is_dir()


def test_server_config_is_loaded(monkeypatch, tmp_path):
    password_data = hash_access_password("server-password")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "profile": "server",
                "host": "127.0.0.1",
                "port": 19001,
                "access_password": password_data,
                "session_secret": "session-secret-at-least-32-characters",
                "trusted_proxies": ["127.0.0.1"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEU_JWXT_PROFILE", "server")
    monkeypatch.setenv("NEU_JWXT_CONFIG", str(config_path))
    monkeypatch.setenv("NEU_JWXT_DATA_DIR", str(tmp_path / "data"))

    from backend.core.runtime.config import get_runtime_config

    config = get_runtime_config()
    assert config.port == 19001
    assert config.access_gateway_enabled
    assert config.access_password_hash == password_data["hash"]


def test_server_runtime_does_not_mutate_read_only_config(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "profile": "server",
                "host": "127.0.0.1",
                "port": 19002,
                "access_password": hash_access_password("server-password"),
                "session_secret": "session-secret-at-least-32-characters",
                "trusted_proxies": ["127.0.0.1"],
            }
        ),
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    monkeypatch.setenv("NEU_JWXT_PROFILE", "server")
    monkeypatch.setenv("NEU_JWXT_CONFIG", str(config_path))
    monkeypatch.setenv("NEU_JWXT_DATA_DIR", str(data_dir))

    original_chmod = Path.chmod

    def reject_config_chmod(path, mode, *, follow_symlinks=True):
        if path == config_path:
            raise OSError(30, "Read-only file system", str(path))
        return original_chmod(path, mode, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "chmod", reject_config_chmod)

    from backend.core.runtime.config import get_runtime_config

    config = get_runtime_config()
    assert config.port == 19002
    assert config.config_file == config_path


def test_shutdown_route_is_desktop_only():
    from backend.app.routers import runtime

    with patch.object(runtime, "config", SimpleNamespace(desktop_mode=False)):
        with pytest.raises(HTTPException) as error:
            asyncio.run(runtime.desktop_shutdown())

    assert error.value.status_code == 404

    timer = Mock()
    with (
        patch.object(runtime, "config", SimpleNamespace(desktop_mode=True)),
        patch.object(runtime.threading, "Timer", return_value=timer) as timer_class,
    ):
        result = asyncio.run(runtime.desktop_shutdown())

    assert result == {"success": True}
    timer_class.assert_called_once()
    timer.start.assert_called_once_with()


def test_config_healthcheck_uses_exact_port_and_ignores_http_proxy(
    monkeypatch,
    tmp_path,
):
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config_path = tmp_path / "compact-config.json"
    config_path.write_text(
        json.dumps(
            {"profile": "server", "host": "127.0.0.1", "port": server.server_port},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update({
        "HTTP_PROXY": "http://127.0.0.1:1",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "NO_PROXY": "",
    })
    try:
        result = subprocess.run(
            [
                sys.executable,
                "launchers/server.py",
                "healthcheck",
                "--config",
                str(config_path),
                "--print-url",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.returncode == 0, result.stderr
    assert f":{server.server_port}/api/health" in result.stdout


def test_linux_upgrade_script_has_transactional_diagnostics():
    packaging_dir = (
        Path(__file__).resolve().parents[1] / "packaging" / "linux"
    )
    script = (packaging_dir / "install.sh").read_text(encoding="utf-8")
    service = (packaging_dir / "neu-jwxt-toolkit.service").read_text(
        encoding="utf-8"
    )

    assert 'healthcheck \\\n    --config "${CONFIG_FILE}"' in script
    assert "seq 1 120" in script
    assert "journalctl -u" in script
    assert "SERVICE_BACKUP=" in script
    assert script.index('chmod 0600 "${CONFIG_FILE}"') < script.index(
        'systemctl restart "${APP_NAME}.service"'
    )
    assert "trap on_install_error ERR" in script
    assert "rollback_install" in script
    assert '配置与数据未改动' in script
    assert "ProtectSystem=strict" in service
    assert "ReadWritePaths=/var/lib/neu-jwxt-toolkit" in service
    assert "ReadWritePaths=/etc/neu-jwxt-toolkit" not in service


def test_windows_upgrade_cleans_only_frozen_program_internals():
    installer = (
        Path(__file__).resolve().parents[1]
        / "packaging"
        / "windows"
        / "installer.iss"
    ).read_text(encoding="utf-8")

    assert "[InstallDelete]" in installer
    assert 'Name: "{app}\\_internal"' in installer
    assert "%LOCALAPPDATA%\\NEU-JWXT-Toolkit\\data" in installer
    assert "cache.db" not in installer
