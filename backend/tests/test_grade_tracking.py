import logging
import re
from types import SimpleNamespace

from backend.core.academic.api import CourseScore
from backend.core.tracking import GradeTrackingService
from backend.core.runtime.access import is_public_api_path
from backend.core.log.access_logger import redact_sensitive_path


def make_score(score="88", gpa=3.8):
    return CourseScore(
        name="软件工程",
        code="A1001",
        score=score,
        gpa=gpa,
        credit=3.0,
        term="2025-2026-2",
        term_display="2025-2026学年春季学期",
        course_type="必修",
        course_category="专业课",
        exam_type="考试",
        is_passed=True,
        exam_status="初修",
    )


class FakeStorage:
    def __init__(self):
        self.saved = []

    def save_scores(self, scores, metadata=None):
        self.saved.append((list(scores), metadata))


class FakeAcademic:
    def __init__(self):
        self.scores = [make_score()]
        self.gpa = 3.8

    def get_scores(self):
        return self.scores

    def get_overall_gpa(self):
        return self.gpa


def build_service(tmp_path):
    academic = FakeAcademic()
    auth = SimpleNamespace(username="20250001", academic=academic)
    storage = FakeStorage()
    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: auth,
        score_storage=storage,
        logger=logging.getLogger("grade-tracking-test"),
    )
    return service, academic, storage


def mail_config(**overrides):
    return {
        "enabled": False,
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "smtp_username": "sender@example.com",
        "smtp_password": "secret",
        "from_email": "sender@example.com",
        "to_email": "receiver@example.com",
        **overrides,
    }


def test_config_never_returns_password_and_blank_preserves_it(tmp_path):
    service, _, _ = build_service(tmp_path)
    result = service.update_config(mail_config())

    assert "smtp_password" not in result
    assert result["smtp_password_configured"] is True

    service.update_config(mail_config(smtp_password=None, interval_minutes=30))
    reloaded = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=FakeStorage(),
        logger=logging.getLogger("grade-tracking-reload-test"),
    )
    assert reloaded.get_config()["smtp_password_configured"] is True
    assert reloaded.get_config()["interval_minutes"] == 30


def test_default_interval_is_thirty_minutes(tmp_path):
    service, _, _ = build_service(tmp_path)

    assert service.get_config()["interval_minutes"] == 30


def test_tracking_switch_applies_immediately_without_changing_config(tmp_path):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config(interval_minutes=45))

    enabled = service.set_enabled(True)
    assert enabled["enabled"] is True
    assert enabled["interval_minutes"] == 45
    assert service.get_status()["stage"] == "scheduled"

    disabled = service.set_enabled(False)
    assert disabled["enabled"] is False
    assert disabled["interval_minutes"] == 45
    assert service.get_status()["stage"] == "disabled"


def test_manual_check_creates_snapshot_then_notifies_changes(tmp_path, monkeypatch):
    service, academic, storage = build_service(tmp_path)
    service.update_config(mail_config())
    sent = []
    monkeypatch.setattr(
        service,
        "_send_email",
        lambda config, subject, body: sent.append((subject, body)),
    )

    first = service.check_now()
    assert first["last_change_count"] == 0
    assert len(sent) == 1
    assert "首次" in sent[0][0]

    academic.scores = [make_score(score="92", gpa=4.2)]
    academic.gpa = 4.2
    second = service.check_now()

    assert second["last_change_count"] == 1
    assert len(second["changes"]) == 1
    assert second["changes"][0]["before"]["score"] == "88"
    assert second["changes"][0]["after"]["score"] == "92"
    assert len(sent) == 2
    assert len(storage.saved) == 2


def test_login_required_does_not_fetch_scores(tmp_path, monkeypatch):
    storage = FakeStorage()
    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=storage,
        logger=logging.getLogger("grade-tracking-login-test"),
    )
    service.update_config(mail_config())
    monkeypatch.setattr(service, "_send_email", lambda config, subject, body: None)

    status = service.check_now()

    assert status["stage"] == "waiting_login"
    assert storage.saved == []


def test_missing_site_url_sends_five_minute_qr_and_resumes_check(tmp_path, monkeypatch):
    academic = FakeAcademic()
    authenticated = SimpleNamespace(username="20250001", academic=academic)
    storage = FakeStorage()
    accepted = []
    sent = []

    class FakeQRClient:
        username = authenticated.username
        academic = authenticated.academic

        def poll_webvpn_qr_login(self, flow_id):
            assert flow_id == "flow-1"
            return {"status": "authenticated"}

        def cancel_webvpn_qr_login(self, flow_id):
            raise AssertionError("authenticated flow must not be cancelled")

    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=storage,
        logger=logging.getLogger("grade-tracking-email-qr-test"),
        qr_login_starter=lambda: (
            FakeQRClient(),
            {
                "flow_id": "flow-1",
                "qr_content": "https://pass.neu.edu.cn/tpass/qyQrLogin?uuid=test",
                "expires_in": 300,
            },
        ),
        auth_setter=accepted.append,
    )
    service.update_config(mail_config(site_url=""))
    monkeypatch.setattr(
        service,
        "_send_email",
        lambda config, subject, body: sent.append((subject, body)),
    )

    result = service.check_now()

    assert len(accepted) == 1
    assert len(storage.saved) == 1
    assert result["stage"] == "monitoring"
    assert "五分钟" in sent[0][0]
    assert "qyQrLogin?uuid=test" in sent[0][1]
    assert "等待下一个成绩检查间隔" in sent[0][1]


def test_interactive_login_flow_is_not_replaced_by_email_qr(tmp_path):
    started = []
    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=FakeStorage(),
        logger=logging.getLogger("grade-tracking-pending-login-test"),
        qr_login_starter=lambda: started.append(True),
        login_flow_pending=lambda: True,
    )
    service.update_config(mail_config(site_url=""))

    result = service.check_now()

    assert result["stage"] == "waiting_login"
    assert "二维码或短信认证" in result["message"]
    assert started == []


def test_tracking_interval_cannot_be_shorter_than_five_minutes(tmp_path):
    service, _, _ = build_service(tmp_path)

    try:
        service.update_config(mail_config(interval_minutes=4))
    except ValueError as error:
        assert "5–1440" in str(error)
    else:
        raise AssertionError("interval shorter than five minutes must be rejected")


def test_configured_site_uses_one_time_page_before_starting_qr(tmp_path, monkeypatch):
    academic = FakeAcademic()
    storage = FakeStorage()
    accepted = []
    sent = []
    starts = []

    class FakeQRClient:
        username = "20250001"

        def __init__(self):
            self.academic = academic

        def poll_webvpn_qr_login(self, flow_id):
            assert flow_id == "recovery-flow"
            return {"status": "authenticated"}

        def cancel_webvpn_qr_login(self, flow_id):
            return None

    def start_qr():
        starts.append(True)
        return FakeQRClient(), {
            "flow_id": "recovery-flow",
            "qr_content": "https://pass.neu.edu.cn/tpass/qyQrLogin?uuid=recovery",
            "expires_in": 300,
            "poll_interval": 3,
        }

    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=storage,
        logger=logging.getLogger("grade-tracking-recovery-page-test"),
        qr_login_starter=start_qr,
        auth_setter=accepted.append,
    )
    service.update_config(
        mail_config(site_url="https://grades.example.com")
    )
    monkeypatch.setattr(
        service,
        "_send_email",
        lambda config, subject, body: sent.append((subject, body)),
    )

    result = service.check_now()

    assert result["stage"] == "waiting_login"
    assert starts == []
    match = re.search(
        r"https://grades\.example\.com/grade-tracking/recovery/([A-Za-z0-9_-]+)",
        sent[0][1],
    )
    assert match
    token = match.group(1)

    flow = service.start_recovery_login(token)
    assert starts == [True]
    assert flow["qr_content"].endswith("uuid=recovery")

    authenticated = service.poll_recovery_login(token)
    assert authenticated["status"] == "authenticated"
    assert len(accepted) == 1
    try:
        service.get_recovery_status(token)
    except ValueError:
        pass
    else:
        raise AssertionError("recovery token must be invalid after authentication")


def test_recovery_api_bypasses_server_password_only_with_token_path():
    assert is_public_api_path(
        "/api/grade-tracking/recovery/token-value/start"
    )
    assert not is_public_api_path("/api/grade-tracking/config")
    assert redact_sensitive_path(
        "/api/grade-tracking/recovery/secret-token/poll"
    ) == "/api/grade-tracking/recovery/<redacted>/poll"
