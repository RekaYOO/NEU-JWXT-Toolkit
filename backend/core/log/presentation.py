"""Convert persisted log messages into concise, user-facing event summaries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


_EVENT_TITLES = {
    "http_access": "HTTP 请求",
    "application_error": "应用异常",
    "unhandled_request_error": "未处理的请求异常",
    "access_gateway_login": "网站访问验证",
    "access_gateway_logout": "退出网站访问",
    "neu_login": "NEU 账号登录",
    "neu_logout": "NEU 账号退出",
    "neu_session_restore": "NEU 会话恢复",
    "webvpn_qr_login": "WebVPN 二维码登录",
    "webvpn_password_login": "WebVPN 密码登录",
    "webvpn_sms_send": "WebVPN 短信发送",
    "webvpn_sms_verify": "WebVPN 短信验证",
    "tracking_recovery_login": "成绩追踪登录恢复",
}

_OUTCOME_LABELS = {
    "success": "成功",
    "failure": "失败",
    "blocked": "已拦截",
    "denied": "已拒绝",
    "error": "异常",
    "pending": "进行中",
}

_REASON_LABELS = {
    "wrong_password": "密码错误",
    "rate_limit": "失败次数过多",
    "gateway_not_configured": "访问密码未配置",
    "login_rejected": "登录被拒绝",
    "webvpn_required": "需要使用 WebVPN",
    "direct_access_failed": "校园网直连失败",
    "request_error": "认证请求失败",
    "unexpected_error": "发生未预期异常",
    "flow_missing": "认证流程不存在",
    "flow_replaced": "认证流程已被替换",
    "qr_start_failed": "二维码流程启动失败",
    "qr_callback_failed": "二维码回调失败",
    "qr_poll_failed": "二维码状态查询失败",
    "qr_cancel_failed": "取消二维码登录失败",
    "sms_send_failed": "短信发送失败",
    "sms_verify_failed": "短信验证失败",
    "sms_cancel_failed": "取消短信验证失败",
    "active_session_restore_failed": "当前会话恢复失败",
    "stored_session_restore_failed": "已保存会话恢复失败",
    "stored_credentials_restore_failed": "已保存凭据恢复失败",
    "recovery_start_failed": "恢复流程启动失败",
    "recovery_poll_failed": "恢复状态查询失败",
}

_GENERIC_TITLES = {
    "access": "访问记录",
    "error": "错误记录",
    "login": "登录记录",
    "sync": "同步记录",
    "system": "系统记录",
}

_JSON_DECODER = json.JSONDecoder()
_JSON_START = re.compile(r"\{")


@dataclass
class LogPresentation:
    """Derived presentation fields; the persisted line remains unchanged."""

    event_type: str
    title: str
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)
    structured: bool = False


def _compact(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _extract_json(message: str) -> Optional[Dict[str, Any]]:
    """Decode the first JSON object, tolerating formatter text after it."""
    match = _JSON_START.search(message)
    if not match:
        return None
    try:
        value, _ = _JSON_DECODER.raw_decode(message[match.start():])
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _http_summary(payload: Dict[str, Any]) -> str:
    method = payload.get("method") or "HTTP"
    path = payload.get("path") or "-"
    status = payload.get("status_code")
    duration = payload.get("response_time_ms")
    parts = [f"{method} {path}"]
    if status is not None:
        parts.append(str(status))
    if duration is not None:
        parts.append(f"{duration} ms")
    return " · ".join(parts)


def _auth_summary(event: str, payload: Dict[str, Any]) -> str:
    outcome = _OUTCOME_LABELS.get(str(payload.get("outcome") or ""), "状态未知")
    parts = [outcome]
    reason = payload.get("reason")
    if reason:
        parts.append(_REASON_LABELS.get(str(reason), str(reason)))
    method = payload.get("auth_method")
    if method and not reason:
        parts.append(str(method))
    return " · ".join(parts)


_DETAIL_FIELDS = {
    "http_access": {
        "event", "request_id", "method", "path", "status_code", "response_time_ms",
        "client_ip", "peer_ip", "user_agent", "user_id", "access_session_id", "scheme",
        "gateway_state", "response_size_bytes",
    },
    "application_error": {
        "event", "error_id", "component", "error_type", "status_code", "request_id",
        "client_ip", "peer_ip", "session_user", "user_agent", "trace",
    },
    "unhandled_request_error": {
        "event", "request_id", "method", "path", "status_code", "client_ip", "peer_ip",
        "user_agent", "session_user",
    },
}


def _safe_details(event: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    fields = _DETAIL_FIELDS.get(event)
    if fields is None:
        fields = {
            "event", "outcome", "request_id", "client_ip", "peer_ip", "access_session_id",
            "user_agent", "subject", "reason", "auth_method", "network_mode", "remember",
            "trust_device", "clear_data", "error_code", "error_type",
        }
    return {key: value for key, value in payload.items() if key in fields}


def present_log_message(message: str, logger: str = "") -> LogPresentation:
    """Match known structured messages and provide a safe generic fallback."""
    payload = _extract_json(message)
    if payload:
        event = str(payload.get("event") or "").strip()
        if event == "http_access":
            return LogPresentation(
                event_type=event,
                title=_EVENT_TITLES[event],
                summary=_http_summary(payload),
                details=_safe_details(event, payload),
                structured=True,
            )
        if event == "application_error":
            component = payload.get("component") or "未知组件"
            error_type = payload.get("error_type") or "UnknownError"
            error_id = payload.get("error_id")
            summary = f"{component} · {error_type}"
            if error_id:
                summary += f" · 编号 {error_id}"
            return LogPresentation(
                event_type=event,
                title=_EVENT_TITLES[event],
                summary=summary,
                details=_safe_details(event, payload),
                structured=True,
            )
        if event == "unhandled_request_error":
            return LogPresentation(
                event_type=event,
                title=_EVENT_TITLES[event],
                summary=f"{payload.get('method', 'HTTP')} {payload.get('path', '-')} · 500",
                details=_safe_details(event, payload),
                structured=True,
            )
        if event in _EVENT_TITLES:
            return LogPresentation(
                event_type=event,
                title=_EVENT_TITLES[event],
                summary=_auth_summary(event, payload),
                details=_safe_details(event, payload),
                structured=True,
            )

    category = "system"
    parts = logger.split(".")
    if len(parts) >= 2 and parts[0] == "neu" and parts[1] in _GENERIC_TITLES:
        category = parts[1]
    return LogPresentation(
        event_type=f"generic_{category}",
        title=_GENERIC_TITLES[category],
        summary=_compact(message) or "空消息",
        details={},
        structured=False,
    )
