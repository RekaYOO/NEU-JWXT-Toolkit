from fastapi.testclient import TestClient
import pytest

from backend.app.dependencies import (
    get_auth_recovery_service,
    get_grade_tracker,
    get_system_mail_service,
)
from backend.app.main import app


class FakeRecoveryService:
    def __init__(self):
        self.calls = []
        self.config = {"public_base_url": "https://toolkit.example.com"}

    def get_status(self, token):
        self.calls.append(("status", token))
        return {"status": "ready"}

    def start(self, token):
        self.calls.append(("start", token))
        return {"status": "pending", "flow_id": "qr-flow", "qr_content": "qr"}

    def poll(self, token):
        self.calls.append(("poll", token))
        return {"status": "pending"}

    def refresh_captcha(self, token):
        self.calls.append(("captcha", token))
        return {"success": True, "captcha_image": "data:image/jpeg;base64,YWJj"}

    def send_sms(self, token, captcha_code):
        self.calls.append(("send", token, captcha_code))
        return {"success": True, "status": "sent"}

    def verify_sms(self, token, code, trust_device=False):
        self.calls.append(("verify", token, code, trust_device))
        return {"status": "authenticated"}

    def cancel(self, token):
        self.calls.append(("cancel", token))
        return {"success": True, "status": "cancelled"}

    def get_config(self):
        return dict(self.config)

    def update_config(self, values):
        self.config = dict(values)
        return dict(self.config)


class FakeMailService:
    def __init__(self):
        self.config = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_security": "ssl",
            "smtp_username": "sender@example.com",
            "from_email": "sender@example.com",
            "to_email": "receiver@example.com",
            "smtp_password_configured": True,
        }
        self.tested = False

    def get_config(self):
        return dict(self.config)

    def update_config(self, values):
        self.config.update({key: value for key, value in values.items() if key != "smtp_password"})
        self.config["smtp_password_configured"] = bool(
            values.get("smtp_password") or self.config.get("smtp_password_configured")
        )
        return dict(self.config)

    def test_email(self):
        self.tested = True


class FakeTracker:
    def update_config(self, values):
        return values


def test_missing_sms_flow_is_restartable_conflict_not_an_invalid_link():
    from backend.app.routers.auth_recovery import _recovery_error
    from backend.core.auth.client import WebVPNLoginError, WEBVPN_ERR_FLOW_MISSING

    error = _recovery_error(WebVPNLoginError(
        "短信验证流程已失效，请在本页重新开始登录", error_code=WEBVPN_ERR_FLOW_MISSING,
    ))
    assert error.status_code == 409
    assert error.detail["error_code"] == WEBVPN_ERR_FLOW_MISSING
    assert _recovery_error(ValueError("一次性登录链接不存在或已失效")).status_code == 404


def test_new_auth_recovery_api_and_system_settings_are_wired():
    recovery = FakeRecoveryService()
    mail = FakeMailService()
    app.dependency_overrides[get_auth_recovery_service] = lambda: recovery
    app.dependency_overrides[get_system_mail_service] = lambda: mail
    try:
        client = TestClient(app)
        token = "context.signature"

        assert client.get(f"/api/auth-recovery/{token}/status").json()["status"] == "ready"
        assert client.post(f"/api/auth-recovery/{token}/start").json()["status"] == "pending"
        assert client.get(f"/api/auth-recovery/{token}/poll").json()["status"] == "pending"
        assert client.post(f"/api/auth-recovery/{token}/captcha/refresh").status_code == 200
        assert client.post(
            f"/api/auth-recovery/{token}/sms/send", json={"captcha_code": "1234"},
        ).json()["status"] == "sent"
        assert client.post(
            f"/api/auth-recovery/{token}/sms/verify",
            json={"code": "654321", "trust_device": True},
        ).json()["status"] == "authenticated"
        assert client.post(f"/api/auth-recovery/{token}/cancel").json()["status"] == "cancelled"

        assert client.get("/api/system-settings/mail").json()["smtp_password_configured"] is True
        assert client.put("/api/system-settings/mail", json={
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_security": "ssl",
            "smtp_username": "sender@example.com",
            "smtp_password": "new-secret",
            "from_email": "sender@example.com",
            "to_email": "receiver@example.com",
        }).status_code == 200
        assert client.post("/api/system-settings/mail/test").status_code == 200
        assert mail.tested is True
        assert client.get("/api/system-settings/auth-recovery").json() == recovery.config
        assert client.put(
            "/api/system-settings/auth-recovery",
            json={"public_base_url": "https://new.example.com"},
        ).json()["config"]["public_base_url"] == "https://new.example.com"
    finally:
        app.dependency_overrides.clear()

    assert recovery.calls == [
        ("status", token),
        ("start", token),
        ("poll", token),
        ("captcha", token),
        ("send", token, "1234"),
        ("verify", token, "654321", True),
        ("cancel", token),
    ]


def test_removed_grade_tracking_recovery_and_mail_apis_return_real_404():
    client = TestClient(app)

    assert client.get(
        "/api/grade-tracking/recovery/legacy-token/status"
    ).status_code == 404
    assert client.post("/api/grade-tracking/test-email").status_code == 404
    assert client.get("/grade-tracking/recovery/legacy-token").status_code == 404


def test_tracking_config_rejects_removed_mail_and_recovery_fields():
    app.dependency_overrides[get_grade_tracker] = lambda: FakeTracker()
    try:
        client = TestClient(app)
        for payload in (
            {"site_url": "https://legacy.example.com"},
            {"smtp_host": "smtp.example.com"},
            {"notify_initial": True},
        ):
            response = client.put("/api/grade-tracking/config", json=payload)
            assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_recovery_ttl_settings_round_trip_and_old_clients_preserve_value(tmp_path):
    from backend.core.auth.recovery import RemoteAuthRecoveryService
    from unittest.mock import Mock

    service = RemoteAuthRecoveryService(tmp_path, mail_service=Mock(), logger=Mock())
    app.dependency_overrides[get_auth_recovery_service] = lambda: service
    try:
        client = TestClient(app)
        path = "/api/system-settings/auth-recovery"
        assert client.get(path).json()["link_ttl_hours"] == 3
        assert client.put(path, json={
            "public_base_url": "https://toolkit.example.com", "link_ttl_hours": 4,
        }).json()["config"]["link_ttl_hours"] == 4
        assert client.put(path, json={
            "public_base_url": "https://new.example.com",
        }).json()["config"]["link_ttl_hours"] == 4
        assert client.put(path, json={
            "link_ttl_hours": 6,
        }).json()["config"]["public_base_url"] == "https://new.example.com"
        assert client.get(path).json()["link_ttl_hours"] == 6
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("hours", [0, -1, 169, True, 1.5, None, "3"])
def test_recovery_api_rejects_invalid_ttl(hours):
    response = TestClient(app).put(
        "/api/system-settings/auth-recovery", json={"link_ttl_hours": hours},
    )
    assert response.status_code == 422
