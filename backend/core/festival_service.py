"""CXCY route preferences and bounded service reads on the shared identity."""

import requests
from backend.core.festival_activities import _looks_like_login

from backend.core.auth.client import (
    DirectAccessError, NEULoginError, WebVPNRequiredError, WEBVPN_ERR_CAMPUS_NETWORK,
)

CONFIG_KEY = "cxcy"


def read_preference(storage):
    config = storage.load_config()
    settings = config.get(CONFIG_KEY, {}) if isinstance(config, dict) else {}
    value = settings.get("network_mode") if isinstance(settings, dict) else None
    return value if value in {"follow", "direct", "webvpn"} else "follow"


def effective_mode(preference, primary_mode):
    return ("webvpn" if primary_mode == "webvpn" else "direct") if preference == "follow" else preference


def failure_state(error):
    code = getattr(error, "error_code", "")
    if code == WEBVPN_ERR_CAMPUS_NETWORK:
        return "campus_network_blocked", code
    if isinstance(error, (requests.Timeout, requests.ConnectionError, DirectAccessError, WebVPNRequiredError)):
        return "network_unreachable", "CXCY_NETWORK_UNREACHABLE"
    if isinstance(error, NEULoginError):
        return "login_required", "CXCY_LOGIN_REQUIRED"
    return "service_unavailable", "CXCY_SERVICE_UNAVAILABLE"


def status_message(state, mode):
    return {
        "checking": "创院线路设置已保存，正在核验可用性。",
        "authenticated": "创院系统会话有效。",
        "login_required": (
            "创院系统的 WebVPN 登录已失效，请使用账号密码或微信扫码恢复。"
            if mode == "webvpn" else "创院系统会话未能建立，请重新检查或切换 WebVPN。"
        ),
        "campus_network_blocked": "校园网环境下学校 WebVPN 不可用，请将创院线路切换为直连或跟随教务。",
        "network_unreachable": (
            "当前网络无法直连创院系统，请切换 WebVPN 或校园网后重试。"
            if mode == "direct" else "当前无法连接学校 WebVPN 或创院系统，请稍后重试。"
        ),
        "service_unavailable": "创院系统暂时不可用，已保存的活动仍可查看。",
    }.get(state, "创院系统暂时不可用。")


class FestivalServiceError(RuntimeError):
    def __init__(self, error, mode):
        self.service_auth_state, self.error_code = failure_state(error)
        self.auth_scope = "cxcy"
        self.response = getattr(error, "response", None)
        super().__init__(status_message(self.service_auth_state, mode))


class FestivalServiceClient:
    """Freeze the route for a complete scan/archive, never the global mode."""

    def __init__(self, auth, storage, *, allow_identity_recovery=True):
        self.auth = auth
        self.allow_identity_recovery = allow_identity_recovery
        self.preference = read_preference(storage)
        self.network_mode = effective_mode(self.preference, getattr(auth, "active_mode", "direct"))

    def __getattr__(self, name):
        return getattr(self.auth, name)

    def request_service(self, service, method, path, **kwargs):
        if service != "cxcy":
            raise ValueError("unsupported festival service")
        try:
            response = self.auth.request_service(
                service, method, path, network_mode_override=self.network_mode, **kwargs,
                allow_identity_recovery=self.allow_identity_recovery,
            )
            try:
                response.raise_for_status()
                if not kwargs.get("stream") and _looks_like_login(response):
                    response.close()
                    raise NEULoginError("创院系统登录已失效")
            except requests.RequestException:
                response.close()
                raise
            return response
        except (NEULoginError, requests.RequestException) as error:
            raise FestivalServiceError(error, self.network_mode) from error
