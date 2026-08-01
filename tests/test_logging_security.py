import asyncio
import json
import logging
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from backend.core.log.access_logger import (
    FastAPILogMiddleware,
    bind_request_context,
    get_request_context,
    reset_request_context,
)
from backend.core.log.logger import (
    DailyRotatingFileHandler,
    LogCategory,
    LogConfig,
)
from backend.core.log.manager import LogManager
from backend.core.log.presentation import present_log_message
from backend.core.log.security_logger import log_application_error, log_security_event


def test_request_context_is_isolated_between_async_tasks():
    async def capture(client_ip):
        token = bind_request_context(client_ip=client_ip)
        try:
            await asyncio.sleep(0)
            return get_request_context()["client_ip"]
        finally:
            reset_request_context(token)

    async def run():
        return await asyncio.gather(capture("192.0.2.1"), capture("192.0.2.2"))

    assert asyncio.run(run()) == ["192.0.2.1", "192.0.2.2"]


def test_security_event_keeps_identity_and_ignores_secret_fields():
    logger = Mock()
    token = bind_request_context(
        request_id="request-1",
        client_ip="198.51.100.8",
        peer_ip="127.0.0.1",
        user_agent="test-agent",
        access_session_id="session-1",
    )
    try:
        with patch("backend.core.log.security_logger.get_logger", return_value=logger):
            log_security_event(
                "neu_login",
                "failure",
                subject="20250001",
                reason="wrong_password",
                password="must-not-appear",
                cookie="must-not-appear",
            )
    finally:
        reset_request_context(token)

    message = logger.log.call_args.args[2]
    payload = json.loads(message)
    assert payload["subject"] == "20250001"
    assert payload["client_ip"] == "198.51.100.8"
    assert payload["access_session_id"] == "session-1"
    assert "must-not-appear" not in message


def test_application_error_keeps_safe_trace_without_exception_message():
    logger = Mock()
    token = bind_request_context(request_id="request-2", client_ip="192.0.2.4")
    try:
        try:
            raise RuntimeError("secret URL https://example.invalid/?ticket=secret")
        except RuntimeError as error:
            with patch("backend.core.log.security_logger.get_logger", return_value=logger):
                error_id = log_application_error("scores.get", error, 500)
    finally:
        reset_request_context(token)

    message = logger.error.call_args.args[1]
    payload = json.loads(message)
    assert error_id == "request-2"
    assert payload["error_type"] == "RuntimeError"
    assert payload["trace"][-1]["function"] == "test_application_error_keeps_safe_trace_without_exception_message"
    assert "ticket=secret" not in message


def test_daily_size_rotation_remains_readable_by_log_manager(tmp_path):
    config = LogConfig(
        log_dir=str(tmp_path),
        max_bytes=300,
        backup_count=2,
        console_output=False,
    )
    handler = DailyRotatingFileHandler(config, LogCategory.SYSTEM)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger = logging.Logger("rotation-test")
    logger.addHandler(handler)
    for index in range(30):
        logger.info("entry-%02d %s", index, "x" * 40)
    handler.close()

    date = datetime.now().strftime("%Y-%m-%d")
    assert list(tmp_path.glob(f"system_{date}.log.*"))
    manager = LogManager(config)
    entries = manager.read_log(LogCategory.SYSTEM, date, limit=1000)
    assert entries
    assert "entry-29" in entries[0].message
    assert "entry-29" in manager.tail_log(LogCategory.SYSTEM, date, 3)[0].message
    assert b"entry-29" in manager.download_log(LogCategory.SYSTEM, date)


def test_unhandled_error_and_access_log_share_request_id():
    async def failing_app(_scope, _receive, _send):
        raise RuntimeError("boom")

    access_logger = Mock()
    system_logger = Mock()
    with (
        patch("backend.core.log.access_logger.AccessLogger", return_value=access_logger),
        patch("backend.core.log.access_logger.get_logger", return_value=system_logger),
    ):
        middleware = FastAPILogMiddleware(failing_app)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/failure",
        "headers": [(b"user-agent", b"error-test")],
        "client": ("192.0.2.9", 1234),
        "scheme": "http",
        "server": ("test", 80),
    }

    async def invoke():
        with pytest.raises(RuntimeError, match="boom"):
            await middleware(scope, Mock(), Mock())

    asyncio.run(invoke())
    error_payload = json.loads(system_logger.exception.call_args.args[1])
    access_fields = access_logger.log_request.call_args.kwargs
    assert error_payload["request_id"] == access_fields["request_id"]
    assert access_fields["status_code"] == 500
    assert access_fields["client_ip"] == "192.0.2.9"


def test_log_presentation_matches_structured_access_event():
    presentation = present_log_message(
        'HTTP {"event":"http_access","method":"GET","path":"/api/scores/cache",'
        '"status_code":200,"response_time_ms":12.34,"client_ip":"192.0.2.8"}',
        "neu.access.access",
    )

    assert presentation.event_type == "http_access"
    assert presentation.title == "HTTP 请求"
    assert presentation.summary == "GET /api/scores/cache · 200 · 12.34 ms"
    assert presentation.details["client_ip"] == "192.0.2.8"
    assert presentation.structured is True


def test_log_presentation_matches_auth_event_and_localizes_reason():
    presentation = present_log_message(
        'AUTH {"event":"access_gateway_login","outcome":"failure",'
        '"reason":"wrong_password","client_ip":"198.51.100.1"}',
        "neu.login.security",
    )

    assert presentation.event_type == "access_gateway_login"
    assert presentation.title == "网站访问验证"
    assert presentation.summary == "失败 · 密码错误"
    assert "20250001" not in presentation.summary


def test_log_presentation_only_returns_allowlisted_details():
    presentation = present_log_message(
        'AUTH {"event":"neu_login","outcome":"success","subject":"20250001",'
        '"request_id":"r1","password":"never-return-this"}',
        "neu.login.security",
    )

    assert presentation.details["subject"] == "20250001"
    assert "password" not in presentation.details


def test_log_presentation_tolerates_context_after_error_json():
    presentation = present_log_message(
        'ERROR {"event":"application_error","error_id":"abc123",'
        '"component":"scores.get","error_type":"RuntimeError"} '
        'context={"request_id":"abc123"}',
        "neu.error.application",
    )

    assert presentation.event_type == "application_error"
    assert presentation.summary == "scores.get · RuntimeError · 编号 abc123"


def test_log_presentation_keeps_unknown_legacy_message_readable():
    presentation = present_log_message(
        "开始 CAS 登录并检查已有会话",
        "neu.system.auth",
    )

    assert presentation.event_type == "generic_system"
    assert presentation.title == "系统记录"
    assert presentation.summary == "开始 CAS 登录并检查已有会话"
    assert presentation.structured is False
