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
from starlette.requests import Request

from backend.core.runtime.access import (
    COOKIE_TTL_SECONDS,
    LoginRateLimiter,
    hash_access_password,
    issue_access_cookie,
    access_cookie_payload,
    request_source,
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
    second_token = issue_access_cookie("test-secret", now=1_000)
    assert token != second_token
    assert access_cookie_payload(token, "test-secret", now=1_001)["session_id"]
    assert validate_access_cookie(token, "test-secret", now=1_001)
    assert not validate_access_cookie(token + "x", "test-secret", now=1_001)
    assert not validate_access_cookie(
        token,
        "test-secret",
        now=1_000 + COOKIE_TTL_SECONDS + 1,
    )
    assert not validate_access_cookie("x" * 2049, "test-secret", now=1_001)


def test_request_source_only_trusts_forwarding_from_configured_proxy():
    spoofed = Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-forwarded-for", b"198.51.100.20")],
        "client": ("203.0.113.10", 1234),
        "scheme": "http",
        "server": ("test", 80),
    })
    config = SimpleNamespace(trusted_proxies=("127.0.0.1",))
    assert request_source(spoofed, config) == "203.0.113.10"

    proxied = Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-forwarded-for", b"198.51.100.20, 127.0.0.1")],
        "client": ("127.0.0.1", 1234),
        "scheme": "http",
        "server": ("test", 80),
    })
    assert request_source(proxied, config) == "198.51.100.20"


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


def test_resource_root_uses_nuitka_standalone_executable_directory(monkeypatch, tmp_path):
    from backend.core.runtime import config as runtime_config

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    executable = tmp_path / "python.exe"
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(
        runtime_config,
        "__compiled__",
        SimpleNamespace(standalone=True),
        raising=False,
    )

    assert runtime_config.resource_root() == tmp_path


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


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.10", "server.example"])
def test_server_runtime_rejects_non_loopback_bind_host(monkeypatch, tmp_path, host):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"profile": "server", "host": host, "port": 19001}),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEU_JWXT_PROFILE", "server")
    monkeypatch.setenv("NEU_JWXT_CONFIG", str(config_path))
    monkeypatch.setenv("NEU_JWXT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HOST", raising=False)

    from backend.core.runtime.config import get_runtime_config

    with pytest.raises(ValueError, match="只允许监听回环地址"):
        get_runtime_config()


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost"])
def test_server_runtime_accepts_loopback_bind_host(monkeypatch, tmp_path, host):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"profile": "server", "host": host, "port": 19001}),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEU_JWXT_PROFILE", "server")
    monkeypatch.setenv("NEU_JWXT_CONFIG", str(config_path))
    monkeypatch.setenv("NEU_JWXT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HOST", raising=False)

    from backend.core.runtime.config import get_runtime_config

    assert get_runtime_config().host == host


def test_server_runtime_rejects_non_loopback_host_environment_override(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"profile": "server", "host": "127.0.0.1", "port": 19001}),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEU_JWXT_PROFILE", "server")
    monkeypatch.setenv("NEU_JWXT_CONFIG", str(config_path))
    monkeypatch.setenv("NEU_JWXT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOST", "0.0.0.0")

    from backend.core.runtime.config import get_runtime_config

    with pytest.raises(ValueError, match="只允许监听回环地址"):
        get_runtime_config()


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

    request = SimpleNamespace(headers={}, app=SimpleNamespace(state=SimpleNamespace()))
    with patch.object(runtime, "config", SimpleNamespace(desktop_mode=False)):
        with pytest.raises(HTTPException) as error:
            asyncio.run(runtime.desktop_shutdown(request))

    assert error.value.status_code == 404

    timer = Mock()
    shutdown = Mock()
    request = SimpleNamespace(
        headers={
            "host": "127.0.0.1:18476",
            "origin": "http://127.0.0.1:18476",
            "x-neu-shutdown-token": "test-token",
        },
        app=SimpleNamespace(state=SimpleNamespace(desktop_shutdown=shutdown)),
    )
    with (
        patch.object(runtime, "config", SimpleNamespace(desktop_mode=True, port=18476)),
        patch.dict(runtime.os.environ, {"NEU_JWXT_SHUTDOWN_TOKEN": "test-token"}),
        patch.object(runtime.threading, "Timer", return_value=timer) as timer_class,
    ):
        result = asyncio.run(runtime.desktop_shutdown(request))

    assert result == {"success": True}
    timer_class.assert_called_once_with(0.1, shutdown)
    timer.start.assert_called_once_with()


@pytest.mark.parametrize(
    ("headers", "status_code"),
    [
        ({"host": "127.0.0.1:18476"}, 403),
        (
            {
                "host": "127.0.0.1:18476",
                "x-neu-shutdown-token": "wrong-token",
            },
            403,
        ),
        (
            {
                "host": "evil.example",
                "x-neu-shutdown-token": "test-token",
            },
            403,
        ),
        (
            {
                "host": "127.0.0.1:18476",
                "origin": "https://evil.example",
                "x-neu-shutdown-token": "test-token",
            },
            403,
        ),
    ],
)
def test_shutdown_route_rejects_untrusted_requests(headers, status_code):
    from backend.app.routers import runtime

    request = SimpleNamespace(
        headers=headers,
        app=SimpleNamespace(state=SimpleNamespace(desktop_shutdown=Mock())),
    )
    with (
        patch.object(runtime, "config", SimpleNamespace(desktop_mode=True, port=18476)),
        patch.dict(runtime.os.environ, {"NEU_JWXT_SHUTDOWN_TOKEN": "test-token"}),
        pytest.raises(HTTPException) as error,
    ):
        asyncio.run(runtime.desktop_shutdown(request))
    assert error.value.status_code == status_code


def test_shutdown_route_requires_ready_graceful_callback():
    from backend.app.routers import runtime

    request = SimpleNamespace(
        headers={
            "host": "127.0.0.1:18476",
            "x-neu-shutdown-token": "test-token",
        },
        app=SimpleNamespace(state=SimpleNamespace()),
    )
    with (
        patch.object(runtime, "config", SimpleNamespace(desktop_mode=True, port=18476)),
        patch.dict(runtime.os.environ, {"NEU_JWXT_SHUTDOWN_TOKEN": "test-token"}),
        pytest.raises(HTTPException) as error,
    ):
        asyncio.run(runtime.desktop_shutdown(request))
    assert error.value.status_code == 503


def test_health_exposes_shutdown_token_only_in_desktop_mode():
    from backend.app.routers import runtime

    with (
        patch.object(runtime, "config", SimpleNamespace(desktop_mode=True, version="1", profile="desktop")),
        patch.dict(runtime.os.environ, {"NEU_JWXT_SHUTDOWN_TOKEN": "test-token"}),
    ):
        assert asyncio.run(runtime.health())["shutdown_token"] == "test-token"
    with patch.object(
        runtime,
        "config",
        SimpleNamespace(desktop_mode=False, version="1", profile="server"),
    ):
        assert "shutdown_token" not in asyncio.run(runtime.health())


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
    # Keep one-cycle cleanup for pre-Nuitka installations, then atomically
    # replace the current standalone runtime directory.
    assert 'Name: "{app}\\_internal"' in installer
    assert 'Name: "{app}\\runtime"' in installer
    assert 'DestDir: "{app}\\runtime"' in installer
    assert 'Filename: "{app}\\runtime\\NEU-JWXT-Toolkit.exe"' in installer
    assert "SetupIconFile=app.ico" in installer
    assert "%LOCALAPPDATA%\\NEU-JWXT-Toolkit\\data" in installer
    assert "cache.db" not in installer
