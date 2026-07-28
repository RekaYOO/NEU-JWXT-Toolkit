import importlib
import json
import os
import asyncio
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
