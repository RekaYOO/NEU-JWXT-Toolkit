import logging
import re
import hashlib
import json
from types import SimpleNamespace

import pytest

from backend.core.academic.api import CourseScore
from backend.core.tracking import GradeTrackingService
from backend.core.runtime.access import is_public_api_path
from backend.core.log.access_logger import redact_sensitive_path
from backend.core.cache.resources import canonicalize_scores, score_to_dict
from backend.app.schemas.tracking import GradeTrackingConfigUpdate


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


class FakeReportStorage:
    def __init__(self):
        self.refreshed = []

    def refresh_report(self, auth):
        self.refreshed.append(auth.username)
        return {"success": True}


class FakeAcademic:
    def __init__(self):
        self.scores = [make_score()]
        self.gpa = 3.8

    def get_scores(self):
        return self.scores

    def get_overall_gpa(self):
        return self.gpa


def score_refresher_for(academic):
    def refresh(_account, _manual):
        payload = canonicalize_scores({
            "scores": [score_to_dict(score) for score in academic.scores],
            "overall_gpa": academic.gpa,
        })
        digest = hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        return {"revision": f"v1:{digest}", "payload": payload}
    return refresh


def build_service(tmp_path):
    academic = FakeAcademic()
    auth = SimpleNamespace(username="20250001", academic=academic)
    storage = FakeStorage()
    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: auth,
        score_storage=storage,
        logger=logging.getLogger("grade-tracking-test"),
        score_refresher=score_refresher_for(academic),
    )
    return service, academic, storage


def deliver_pending_email(service):
    service._send_email = lambda *_args, **_kwargs: None
    service._flush_outbox()


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
        "start_hour": 0,
        "end_hour": 24,
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


def test_tracking_update_payload_does_not_materialize_unset_smtp_defaults():
    payload = GradeTrackingConfigUpdate(interval_minutes=45, start_hour=8, end_hour=22)
    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    assert values == {"interval_minutes": 45, "start_hour": 8, "end_hour": 22}


def test_default_interval_is_thirty_minutes(tmp_path):
    service, _, _ = build_service(tmp_path)

    assert service.get_config()["interval_minutes"] == 30


def test_invalid_legacy_tracking_files_do_not_break_startup(tmp_path):
    (tmp_path / "grade_tracking_config.json").write_text(
        '{"interval_minutes":"invalid"}',
        encoding="utf-8",
    )
    (tmp_path / "grade_tracking_state.json").write_text("[]", encoding="utf-8")
    (tmp_path / "grade_tracking_outbox.json").write_text(
        '{"messages":"invalid"}',
        encoding="utf-8",
    )

    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=FakeStorage(),
        logger=logging.getLogger("grade-tracking-legacy-data-test"),
    )

    assert service.get_config()["interval_minutes"] == 30
    assert service.get_status()["pending_notifications"] == 0
    assert service.get_status()["stage"] == "disabled"


def test_tracking_refreshes_shared_report_cache_only_when_scores_change(tmp_path):
    academic = FakeAcademic()
    auth = SimpleNamespace(username="20250001", academic=academic)
    report_storage = FakeReportStorage()
    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: auth,
        score_storage=FakeStorage(),
        report_storage=report_storage,
        logger=logging.getLogger("grade-tracking-report-sync-test"),
        score_refresher=score_refresher_for(academic),
    )
    service._flush_outbox = lambda: None

    service.check_now()
    service.check_now()
    academic.scores = [make_score(score="93", gpa=4.3)]
    academic.gpa = 4.3
    service.check_now()

    assert report_storage.refreshed == []


def test_report_refresh_exception_does_not_break_score_sync(tmp_path):
    academic = FakeAcademic()
    auth = SimpleNamespace(username="20250001", academic=academic)

    class FailingReportStorage:
        def refresh_report(self, _auth):
            raise RuntimeError("temporary report failure")

    storage = FakeStorage()
    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: auth,
        score_storage=storage,
        report_storage=FailingReportStorage(),
        logger=logging.getLogger("grade-tracking-report-failure-test"),
        score_refresher=score_refresher_for(academic),
    )
    service._flush_outbox = lambda: None

    result = service.check_now()

    assert result["stage"] == "monitoring"
    assert len(storage.saved) == 0


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


def test_failed_enable_does_not_mutate_memory_or_disk(tmp_path):
    service, _, _ = build_service(tmp_path)

    with pytest.raises(ValueError, match="启用前请填写"):
        service.set_enabled(True)

    assert service.get_config()["enabled"] is False
    assert service.get_status()["enabled"] is False
    assert service.get_status()["stage"] == "disabled"
    assert not service.config_path.exists()


def test_config_write_failure_does_not_apply_enable_in_memory(
    tmp_path,
    monkeypatch,
):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config())
    original_write = service._write_json

    def fail_config_write(path, value):
        if path == service.config_path:
            raise OSError("disk unavailable")
        return original_write(path, value)

    monkeypatch.setattr(service, "_write_json", fail_config_write)

    with pytest.raises(OSError, match="disk unavailable"):
        service.set_enabled(True)

    assert service.get_config()["enabled"] is False
    assert service.get_status()["enabled"] is False


def test_each_enable_transition_queues_a_fresh_initial_email(
    tmp_path,
    monkeypatch,
):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config())
    service.resume_after_login("20250001")
    payload = canonicalize_scores({
        "scores": [score_to_dict(make_score())],
        "overall_gpa": 3.8,
    })
    monkeypatch.setattr(service, "_within_window", lambda _now: False)

    service.set_enabled(True)
    assert service._should_run() is True
    service._state["stage"] = "waiting_login"
    assert service._should_run() is False
    service._state["stage"] = "scheduled"
    service.handle_scores_revision(
        "20250001", "v1:baseline", payload, reason="tracking"
    )

    assert len(service._outbox) == 1
    first_key = service._outbox[0]["dedupe_key"]
    assert first_key.startswith("activation:")
    assert "初始" in service._outbox[0]["subject"]

    deliver_pending_email(service)
    service.set_enabled(True)
    service.handle_scores_revision(
        "20250001", "v1:baseline", payload, reason="tracking"
    )
    assert service._outbox == []

    service.set_enabled(False)
    service.set_enabled(True)
    service.handle_scores_revision(
        "20250001", "v1:baseline", payload, reason="tracking"
    )

    assert len(service._outbox) == 1
    assert service._outbox[0]["dedupe_key"].startswith("activation:")
    assert service._outbox[0]["dedupe_key"] != first_key
    service.set_enabled(False)
    assert service._outbox == []


def test_repeated_enable_and_config_save_preserve_waiting_login(tmp_path):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config())
    service.set_enabled(True)
    waiting_until = "2099-01-01T00:00:00+08:00"
    service._state.update(stage="waiting_login", next_check_at=waiting_until)

    service.set_enabled(True)
    service.update_config({"interval_minutes": 45})

    assert service.get_status()["stage"] == "waiting_login"
    assert service.get_status()["next_check_at"] == waiting_until
    assert service._should_run() is False


def test_activation_retries_until_smtp_success(tmp_path):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config())
    service.resume_after_login("20250001")
    service.set_enabled(True)
    activation_id = service._config["_activation_id"]
    service.handle_scores_revision(
        "20250001",
        "v1:baseline",
        canonicalize_scores({
            "scores": [score_to_dict(make_score())],
            "overall_gpa": 3.8,
        }),
        reason="tracking",
    )
    attempts = []

    def fail_once(*_args):
        attempts.append("failed")
        raise OSError("smtp unavailable")

    service._send_email = fail_once
    service._flush_outbox()
    assert service._config["_activation_id"] == activation_id
    assert service._outbox[0]["attempts"] == 1

    service.handle_scores_revision(
        "20250001",
        "v1:changed",
        canonicalize_scores({
            "scores": [score_to_dict(make_score(score="92", gpa=4.2))],
            "overall_gpa": 4.2,
        }),
        reason="tracking",
    )
    assert len(service._outbox) == 2
    assert service._outbox[1]["dedupe_key"] == "revision:v1:changed"

    deliver_pending_email(service)
    assert "_activation_id" not in service._config
    assert len(service._outbox) == 1


def test_delivered_activation_is_not_resent_after_outbox_write_failure(
    tmp_path,
    monkeypatch,
):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config())
    service.resume_after_login("20250001")
    service.set_enabled(True)
    service.handle_scores_revision(
        "20250001",
        "v1:baseline",
        canonicalize_scores({
            "scores": [score_to_dict(make_score())],
            "overall_gpa": 3.8,
        }),
        reason="tracking",
    )
    service._send_email = lambda *_args: None
    monkeypatch.setattr(
        service,
        "_save_outbox",
        lambda: (_ for _ in ()).throw(OSError("outbox unavailable")),
    )

    with pytest.raises(OSError, match="outbox unavailable"):
        service._flush_outbox()

    restarted, _, _ = build_service(tmp_path)
    sent = []
    restarted._send_email = lambda *_args: sent.append(True)
    restarted._flush_outbox()

    assert sent == []
    assert restarted._outbox == []
    assert "_activation_id" not in restarted._config


def test_clear_personal_state_preserves_pending_activation_intent(tmp_path):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config())
    service.set_enabled(True)
    activation_id = service._config["_activation_id"]
    service._outbox.append({
        "id": "old-account-mail",
        "dedupe_key": f"activation:{activation_id}",
    })

    service.pause_for_logout(clear_personal_state=True)

    assert service._outbox == []
    assert service._config["_activation_id"] == activation_id


def test_disabling_during_snapshot_build_cancels_activation_commit(
    tmp_path,
    monkeypatch,
):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config())
    service.resume_after_login("20250001")
    service.set_enabled(True)
    original_build_snapshot = service._build_snapshot

    def build_then_disable(*args, **kwargs):
        snapshot = original_build_snapshot(*args, **kwargs)
        service.set_enabled(False)
        return snapshot

    monkeypatch.setattr(service, "_build_snapshot", build_then_disable)
    service.handle_scores_revision(
        "20250001",
        "v1:baseline",
        canonicalize_scores({
            "scores": [score_to_dict(make_score())],
            "overall_gpa": 3.8,
        }),
        reason="tracking",
    )

    assert service.get_config()["enabled"] is False
    assert service._outbox == []


@pytest.mark.parametrize("failing_save", ["outbox", "state"])
def test_partial_enable_write_is_recoverable(tmp_path, monkeypatch, failing_save):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config())
    original_save = getattr(service, f"_save_{failing_save}")
    monkeypatch.setattr(
        service,
        f"_save_{failing_save}",
        lambda: (_ for _ in ()).throw(OSError(f"{failing_save} unavailable")),
    )

    with pytest.raises(OSError, match="unavailable"):
        service.set_enabled(True)

    persisted = json.loads(service.config_path.read_text(encoding="utf-8"))
    assert persisted["enabled"] is True
    assert persisted["_activation_id"] == service._config["_activation_id"]

    restarted, _, _ = build_service(tmp_path)
    monkeypatch.setattr(restarted, "_within_window", lambda _now: False)
    assert restarted.get_status()["stage"] == "scheduled"
    assert restarted._should_run() is True

    monkeypatch.setattr(service, f"_save_{failing_save}", original_save)
    service.set_enabled(True)
    assert service.get_status()["stage"] == "scheduled"


def test_config_api_cannot_overwrite_enabled_switch():
    legacy_payload = GradeTrackingConfigUpdate.model_validate({
        "enabled": True,
        "notify_initial": False,
    })

    assert "enabled" not in legacy_payload.model_dump()
    assert "notify_initial" not in legacy_payload.model_dump()


def test_account_switch_rebuilds_pending_activation_for_new_account(tmp_path):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config(enabled=True, notify_initial=True))
    service.resume_after_login("account-a")
    payload_a = canonicalize_scores({
        "scores": [score_to_dict(make_score(score="88", gpa=3.8))],
        "overall_gpa": 3.8,
    })
    service.handle_scores_revision(
        "account-a", "v1:account-a", payload_a, reason="tracking"
    )
    assert service._outbox
    assert service.snapshot_path.exists()

    service.pause_for_logout(clear_personal_state=False)
    service.resume_after_login("account-b")

    assert service.get_config()["enabled"] is True
    assert service._outbox == []
    assert not service.snapshot_path.exists()
    payload_b = canonicalize_scores({
        "scores": [score_to_dict(make_score(score="95", gpa=4.5))],
        "overall_gpa": 4.5,
    })
    result = service.handle_scores_revision(
        "account-b", "v1:account-b", payload_b, reason="login_bootstrap"
    )
    assert result["change_count"] == 0
    assert len(service._outbox) == 1
    assert service._outbox[0]["dedupe_key"].startswith("activation:")
    assert "初始" in service._outbox[0]["subject"]


def test_unscoped_legacy_tracking_state_is_not_claimed_by_new_account(tmp_path):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config(enabled=True, notify_initial=True))
    service._write_json(
        service.snapshot_path,
        service._build_snapshot(
            [score_to_dict(make_score())], 3.8, "legacy-revision"
        ),
    )
    service._state.update(
        account_id="",
        last_seen_revision="legacy-revision",
        last_notified_revision="legacy-revision",
    )
    service._save_state()

    service.resume_after_login("account-b")

    assert not service.snapshot_path.exists()
    assert service.get_status()["account_id"] == "account-b"
    assert service.get_status().get("last_seen_revision") is None


def test_manual_check_creates_snapshot_then_notifies_changes(tmp_path, monkeypatch):
    service, academic, storage = build_service(tmp_path)
    service.update_config(mail_config(enabled=True))
    sent = []
    monkeypatch.setattr(
        service,
        "_send_email",
        lambda config, subject, body: sent.append((subject, body)),
    )

    first = service.check_now()
    service._flush_outbox()
    assert first["last_change_count"] == 0
    assert len(sent) == 1
    assert "初始" in sent[0][0]

    academic.scores = [make_score(score="92", gpa=4.2)]
    academic.gpa = 4.2
    second = service.check_now()
    service._flush_outbox()

    assert second["last_change_count"] == 2
    assert len(second["changes"]) == 1
    assert second["changes"][0]["before"]["score"] == "88"
    assert second["changes"][0]["after"]["score"] == "92"
    assert len(sent) == 2
    assert len(storage.saved) == 0


def test_tracking_notifies_overall_gpa_only_change(tmp_path):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config(enabled=True, notify_initial=False))
    service.resume_after_login("20250001")
    base = canonicalize_scores({
        "scores": [score_to_dict(make_score())],
        "overall_gpa": 3.8,
    })
    service.handle_scores_revision(
        "20250001", "v1:base", base, reason="tracking"
    )
    deliver_pending_email(service)
    updated = {**base, "overall_gpa": 3.9}

    result = service.handle_scores_revision(
        "20250001", "v1:gpa-only", updated, reason="page_swr"
    )

    assert result["overall_gpa_changed"] is True
    assert result["change_count"] == 1
    assert len(service._outbox) == 1
    assert "总 GPA 是否变化：是" in service._outbox[0]["body"]


def test_tracking_notifies_non_score_course_field_change(tmp_path):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config(enabled=True, notify_initial=False))
    service.resume_after_login("20250001")
    base_score = score_to_dict(make_score())
    base = canonicalize_scores({
        "scores": [base_score],
        "overall_gpa": 3.8,
    })
    service.handle_scores_revision(
        "20250001", "v1:base", base, reason="tracking"
    )
    deliver_pending_email(service)
    changed = canonicalize_scores({
        "scores": [{**base_score, "exam_status": "重修"}],
        "overall_gpa": 3.8,
    })

    result = service.handle_scores_revision(
        "20250001", "v1:status", changed, reason="page_swr"
    )

    assert result["change_count"] == 1
    assert len(service._outbox) == 1
    assert "考试状态" in service._outbox[0]["body"]


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
        score_refresher=score_refresher_for(academic),
    )
    service.update_config(mail_config(site_url=""))
    monkeypatch.setattr(
        service,
        "_send_email",
        lambda config, subject, body: sent.append((subject, body)),
    )

    result = service.check_now()
    service._flush_outbox()

    assert len(accepted) == 1
    assert len(storage.saved) == 0
    assert result["stage"] == "monitoring"
    assert "五分钟" in sent[0][0]
    assert "微信扫码" in sent[0][1]
    assert "NEU Pass" not in sent[0][1]
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


def test_test_email_is_a_general_system_mail_check(tmp_path, monkeypatch):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config())
    sent = []
    monkeypatch.setattr(service, "_send_email", lambda config, subject, body: sent.append((subject, body)))
    service.test_email()
    assert sent[0][0] == "[NEU 教务工具箱] 系统邮件配置测试"
    assert "SMTP" in sent[0][1]
    assert "成绩发生变化" not in sent[0][1]
    assert "具体业务功能" in sent[0][1]


def test_system_notification_outbox_delivers_plain_and_html_alternatives(tmp_path, monkeypatch):
    service, _, _ = build_service(tmp_path)
    service.update_config(mail_config())
    sent = []
    monkeypatch.setattr(
        service, "_send_email",
        lambda config, subject, body, html_body="": sent.append((subject, body, html_body)),
    )

    assert service.queue_system_notification(
        "选课提醒", "纯文本内容", "batch:event", "<html><body>美观内容</body></html>",
    ) is True
    service._flush_outbox()

    assert sent == [("选课提醒", "纯文本内容", "<html><body>美观内容</body></html>")]


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
    assert "微信扫码二维码" in sent[0][1]
    assert "NEU Pass" not in sent[0][1]

    flow = service.start_recovery_login(token)
    assert starts == [True]
    assert flow["qr_content"].endswith("uuid=recovery")

    refreshed_flow = service.start_recovery_login(token)
    assert starts == [True, True]
    assert refreshed_flow["qr_content"].endswith("uuid=recovery")

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


def build_detail_tracking_service(tmp_path, lookup):
    service, _, _ = build_service(tmp_path)
    service.score_detail_lookup = lookup
    service.update_config(mail_config(enabled=True, notify_initial=True))
    service.resume_after_login("20250001")
    return service


def tracking_payload(scores, overall_gpa=3.8):
    return canonicalize_scores({
        "scores": [
            score_to_dict(score) if isinstance(score, CourseScore) else score
            for score in scores
        ],
        "overall_gpa": overall_gpa,
    })


def test_tracking_initialization_never_queries_score_details(tmp_path):
    calls = []
    service = build_detail_tracking_service(
        tmp_path,
        lambda account, course: calls.append((account, course)),
    )

    result = service.handle_scores_revision(
        "20250001",
        "v1:initial",
        tracking_payload([make_score()]),
        reason="tracking",
    )

    assert result["change_count"] == 0
    assert calls == []
    assert len(service._outbox) == 1
    assert "_score_detail" not in service._outbox[0]["body"]


def test_tracking_queries_added_course_and_includes_detail_in_email(tmp_path):
    calls = []

    def lookup(account, course):
        calls.append((account, course["code"], course["term"]))
        return {
            "status": "available",
            "item_scores": [
                {"code": "DAILY", "name": "平时成绩", "value": "92"},
                {"code": "FINAL", "name": "期末成绩", "value": "81"},
            ],
        }

    service = build_detail_tracking_service(tmp_path, lookup)
    base = tracking_payload([make_score()])
    service.handle_scores_revision(
        "20250001", "v1:base", base, reason="tracking"
    )
    deliver_pending_email(service)
    added = {
        **score_to_dict(make_score(score="81", gpa=3.1)),
        "name": "新增课程",
        "code": "B2002",
    }

    result = service.handle_scores_revision(
        "20250001",
        "v1:added",
        tracking_payload([make_score(), added]),
        reason="tracking",
    )

    assert result["change_count"] == 1
    assert calls == [("20250001", "B2002", "2025-2026-2")]
    assert "平时成绩 92" in service._outbox[0]["body"]
    assert "期末成绩 81" in service._outbox[0]["body"]


@pytest.mark.parametrize(
    ("new_score", "new_gpa"),
    [("91", 3.8), ("88", 4.0)],
)
def test_tracking_queries_details_when_score_or_gpa_changes(
    tmp_path, new_score, new_gpa
):
    calls = []

    def lookup(_account, course):
        calls.append((course["score"], course["gpa"]))
        return {"status": "no_data", "item_scores": []}

    service = build_detail_tracking_service(tmp_path, lookup)
    service.handle_scores_revision(
        "20250001",
        "v1:base",
        tracking_payload([make_score()]),
        reason="tracking",
    )
    deliver_pending_email(service)

    service.handle_scores_revision(
        "20250001",
        f"v1:changed-{new_score}-{new_gpa}",
        tracking_payload([make_score(score=new_score, gpa=new_gpa)]),
        reason="tracking",
    )

    assert calls == [(new_score, new_gpa)]
    assert "暂无可用分项成绩" in service._outbox[0]["body"]


def test_tracking_other_course_field_change_does_not_query_details(tmp_path):
    calls = []
    service = build_detail_tracking_service(
        tmp_path,
        lambda account, course: calls.append((account, course)),
    )
    base_score = score_to_dict(make_score())
    service.handle_scores_revision(
        "20250001",
        "v1:base",
        tracking_payload([base_score]),
        reason="tracking",
    )
    deliver_pending_email(service)

    result = service.handle_scores_revision(
        "20250001",
        "v1:status",
        tracking_payload([{**base_score, "exam_status": "重修"}]),
        reason="tracking",
    )

    assert result["change_count"] == 1
    assert calls == []
    assert len(service._outbox) == 1


def test_tracking_detail_failure_does_not_block_score_notification(tmp_path):
    def failing_lookup(_account, _course):
        raise RuntimeError("remote detail unavailable")

    service = build_detail_tracking_service(tmp_path, failing_lookup)
    service.handle_scores_revision(
        "20250001",
        "v1:base",
        tracking_payload([make_score()]),
        reason="tracking",
    )
    deliver_pending_email(service)

    result = service.handle_scores_revision(
        "20250001",
        "v1:score-change",
        tracking_payload([make_score(score="93", gpa=4.3)], overall_gpa=4.3),
        reason="tracking",
    )

    assert result["change_count"] == 2
    assert len(service._outbox) == 1
    assert "本次未能获取分项成绩" in service._outbox[0]["body"]
