import logging
import re
import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.core.academic.api import CourseScore
from backend.core.tracking import GradeTrackingService as CoreGradeTrackingService
from backend.core.notifications import SystemMailService
from backend.core.auth.recovery import RemoteAuthRecoveryService
from backend.core.runtime.access import is_public_api_path
from backend.core.log.access_logger import redact_sensitive_path
from backend.core.cache.resources import canonicalize_scores, score_to_dict
from backend.app.schemas.tracking import GradeTrackingConfigUpdate


class TrackingTestFixture:
    """Compose the three independent services for legacy behavioral cases."""

    def __init__(self, data_dir, auth_provider, score_storage, logger, **kwargs):
        qr_login_starter = kwargs.pop("qr_login_starter", None)
        auth_setter = kwargs.pop("auth_setter", None)
        pending_sms_provider = kwargs.pop("pending_sms_provider", None)
        remote_guard = kwargs.pop("remote_guard", None)
        auth_error_code_provider = kwargs.pop("auth_error_code_provider", None)
        self.mail_service = SystemMailService(data_dir, logger)
        self.recovery_service = RemoteAuthRecoveryService(
            data_dir,
            mail_service=self.mail_service,
            logger=logger,
            qr_login_starter=qr_login_starter,
            auth_committer=auth_setter,
            pending_sms_provider=pending_sms_provider,
            remote_guard=remote_guard,
            auth_error_code_provider=auth_error_code_provider,
        )
        self.tracker = CoreGradeTrackingService(
            data_dir=data_dir,
            auth_provider=auth_provider,
            score_storage=score_storage,
            logger=logger,
            mail_service=self.mail_service,
            auth_recovery_service=self.recovery_service,
            **kwargs,
        )
        self.mail_service.register_delivery_listener(
            "grade_tracking", self.tracker.handle_mail_delivered,
        )
        self.mail_service.register_delivery_validator(
            "grade_tracking", self.tracker.should_deliver_mail,
        )
        self.recovery_service.register_recovered_callback(
            "grade_tracking", self.tracker.resume_after_login,
        )

    def __getattr__(self, name):
        recovery_methods = {
            "start_recovery_login": "start",
            "poll_recovery_login": "poll",
            "refresh_recovery_captcha": "refresh_captcha",
            "send_recovery_sms": "send_sms",
            "verify_recovery_sms": "verify_sms",
            "cancel_recovery_login": "cancel",
            "get_recovery_status": "get_status",
        }
        if name in recovery_methods:
            return getattr(self.recovery_service, recovery_methods[name])
        if name == "queue_system_notification":
            return lambda subject, body, dedupe_key, html_body="": self.mail_service.queue_notification(
                "course_selection", subject, body, f"course-selection:{dedupe_key}", html_body,
            )
        if name == "request_auth_recovery_notification":
            return lambda **payload: self.recovery_service.request_notification(
                source="course_selection",
                account_id=str(self.tracker._state.get("account_id") or "20250001"),
                **payload,
            )
        if name == "_flush_outbox":
            return self.mail_service.flush_once
        if name == "_send_email":
            return self.mail_service._send_email
        if name == "_save_outbox":
            return self.mail_service._save_outbox
        if name == "_invalidate_recovery_link" or name == "invalidate_recovery_link":
            return self.recovery_service.invalidate_all
        if name == "test_email":
            return self.mail_service.test_email
        if name == "_recovery_flow":
            record = next(iter(self.recovery_service._flows.values()), None)
            return record.get("flow") if record else None
        return getattr(self.tracker, name)

    def __setattr__(self, name, value):
        if name in {"mail_service", "recovery_service", "tracker"}:
            object.__setattr__(self, name, value)
        elif name == "_send_email":
            self.mail_service._send_email = value
        elif name == "_flush_outbox":
            self.mail_service.flush_once = value
        elif name == "_save_outbox":
            self.mail_service._save_outbox = value
        else:
            setattr(self.tracker, name, value)

    @property
    def _outbox(self):
        return self.mail_service._outbox

    @_outbox.setter
    def _outbox(self, value):
        self.mail_service._outbox = value

    @property
    def outbox_path(self):
        return self.mail_service.outbox_path

    def update_config(self, values):
        values = dict(values)
        mail_fields = {
            key: values.pop(key) for key in list(values)
            if key.startswith("smtp_") or key in {"from_email", "to_email", "clear_smtp_password"}
        }
        if mail_fields:
            self.mail_service.update_config(mail_fields)
        if "site_url" in values:
            self.recovery_service.update_config({"public_base_url": values.pop("site_url")})
        return self.tracker.update_config(values)


def GradeTrackingService(*args, **kwargs):
    return TrackingTestFixture(*args, **kwargs)


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
    service.update_config(mail_config())
    result = service.mail_service.get_config()

    assert "smtp_password" not in result
    assert result["smtp_password_configured"] is True

    service.update_config(mail_config(smtp_password=None, interval_minutes=30))
    reloaded = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=FakeStorage(),
        logger=logging.getLogger("grade-tracking-reload-test"),
    )
    assert reloaded.mail_service.get_config()["smtp_password_configured"] is True


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

    with pytest.raises(ValueError, match="系统设置"):
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
        "source": "grade_tracking",
        "kind": "message",
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


def test_config_api_rejects_removed_switch_and_initial_notification_fields():
    with pytest.raises(ValidationError):
        GradeTrackingConfigUpdate.model_validate({
            "enabled": True,
            "notify_initial": False,
        })


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


def test_missing_site_url_sends_manual_login_notice_once(tmp_path, monkeypatch):
    storage = FakeStorage()
    sent = []
    started = []

    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=storage,
        logger=logging.getLogger("grade-tracking-manual-login-notice-test"),
        qr_login_starter=lambda: started.append(True),
    )
    service.update_config(mail_config(site_url=""))
    monkeypatch.setattr(
        service,
        "_send_email",
        lambda config, subject, body: sent.append((subject, body)),
    )

    first = service.check_now()
    service._flush_outbox()
    second = service.check_now()
    service._flush_outbox()

    assert first["stage"] == second["stage"] == "waiting_login"
    assert storage.saved == []
    assert started == []
    assert len(sent) == 1
    assert sent[0][0] == "[NEU 成绩追踪] 登录已失效"
    assert "进入 NEU 教务工具箱手动登录" in sent[0][1]
    assert "二维码" not in sent[0][1]

    service.resume_after_login("20250001")
    service.check_now()
    service._flush_outbox()
    assert len(sent) == 2


def test_interactive_login_flow_is_not_replaced_and_user_is_notified(tmp_path, monkeypatch):
    started = []
    sent = []
    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=FakeStorage(),
        logger=logging.getLogger("grade-tracking-pending-login-test"),
        qr_login_starter=lambda: started.append(True),
        login_flow_pending=lambda: True,
    )
    service.update_config(mail_config(site_url=""))
    monkeypatch.setattr(
        service,
        "_send_email",
        lambda config, subject, body: sent.append((subject, body)),
    )

    result = service.check_now()
    service._flush_outbox()

    assert result["stage"] == "waiting_login"
    assert "登录已失效" in result["message"]
    assert started == []
    assert len(sent) == 1


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
        logger=logging.getLogger("auth-recovery-page-test"),
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
    service._flush_outbox()

    assert result["stage"] == "waiting_login"
    assert starts == []
    match = re.search(
        r"https://grades\.example\.com/auth-recovery/([A-Za-z0-9_.-]+)",
        sent[0][1],
    )
    assert match
    token = match.group(1)
    assert "一次性页面恢复登录" in sent[0][1]
    assert "图形验证码" in sent[0][1]
    assert "短信验证码" in sent[0][1]
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


def test_recovery_qr_can_continue_through_sms_challenge(tmp_path, monkeypatch):
    accepted = []
    sent = []

    class FakeSMSClient:
        username = "20250001"

        def __init__(self):
            self.cancelled = []
            self.poll_count = 0

        @property
        def is_logged_in(self):
            return True

        def poll_webvpn_qr_login(self, flow_id):
            assert flow_id == "qr-flow"
            self.poll_count += 1
            return {
                "status": "sms_required",
                "flow_id": "sms-flow",
                "captcha_image": "data:image/jpeg;base64,abc",
                "expires_in": 180,
            }

        def refresh_webvpn_captcha(self, flow_id):
            assert flow_id == "sms-flow"
            return {
                "status": "captcha_refreshed",
                "captcha_image": "data:image/jpeg;base64,new",
            }

        def send_webvpn_sms_code(self, flow_id, captcha_code):
            assert (flow_id, captcha_code) == ("sms-flow", "1234")
            return {"status": "sent", "expires_in": 300}

        def verify_webvpn_sms_code(self, flow_id, code, trust_device=False):
            assert (flow_id, code, trust_device) == ("sms-flow", "246810", False)
            return {"status": "authenticated", "username": self.username}

        def cancel_webvpn_qr_login(self, flow_id):
            self.cancelled.append(("qr", flow_id))

        def cancel_webvpn_sms_login(self, flow_id):
            self.cancelled.append(("sms", flow_id))

    client = FakeSMSClient()
    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=FakeStorage(),
        logger=logging.getLogger("grade-tracking-sms-recovery-test"),
        qr_login_starter=lambda: (
            client,
            {
                "flow_id": "qr-flow",
                "qr_content": "https://pass.neu.edu.cn/qr",
                "expires_in": 300,
                "poll_interval": 3,
            },
        ),
        auth_setter=accepted.append,
    )
    service.update_config(mail_config(site_url="https://grades.example.com"))
    monkeypatch.setattr(
        service,
        "_send_email",
        lambda config, subject, body: sent.append((subject, body)),
    )
    service.check_now()
    service._flush_outbox()
    token = re.search(
        r"/auth-recovery/([A-Za-z0-9_.-]+)", sent[0][1]
    ).group(1)

    service.start_recovery_login(token)
    challenge = service.poll_recovery_login(token)
    assert challenge["status"] == "sms_required"
    assert service.get_recovery_status(token)["captcha_image"].endswith("abc")

    refreshed = service.refresh_recovery_captcha(token)
    assert refreshed["captcha_image"].endswith("new")
    assert service.send_recovery_sms(token, "1234")["status"] == "sent"
    renewed = service.get_recovery_status(token)
    assert 295 <= renewed["expires_in"] <= 300
    completed = service.verify_recovery_sms(token, "246810")

    assert completed["status"] == "authenticated"
    assert accepted == [client]
    assert service.get_status()["stage"] == "waiting_login"
    with pytest.raises(ValueError):
        service.get_recovery_status(token)


def test_saved_credentials_sms_challenge_is_reused_without_new_qr(tmp_path, monkeypatch):
    sent = []
    starts = []

    class PendingSMSClient:
        username = "20250001"

        def cancel_webvpn_sms_login(self, _flow_id):
            return None

    client = PendingSMSClient()
    challenge = {
        "status": "sms_required",
        "flow_id": "saved-password-sms",
        "captcha_image": "data:image/jpeg;base64,pending",
        "expires_in": 180,
    }
    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=FakeStorage(),
        logger=logging.getLogger("grade-tracking-adopt-sms-test"),
        qr_login_starter=lambda: starts.append(True),
        login_flow_pending=lambda: True,
        pending_sms_provider=lambda: (client, challenge),
    )
    service.update_config(mail_config(site_url="https://grades.example.com"))
    monkeypatch.setattr(
        service,
        "_send_email",
        lambda config, subject, body: sent.append((subject, body)),
    )

    result = service.check_now()
    service._flush_outbox()
    token = re.search(
        r"/auth-recovery/([A-Za-z0-9_.-]+)", sent[0][1]
    ).group(1)
    status = service.get_recovery_status(token)

    assert result["stage"] == "waiting_login"
    assert status["status"] == "sms_required"
    assert status["flow_id"] == "saved-password-sms"
    assert starts == []
    assert "已使用保存的账号信息" in sent[0][1]
    assert "直接填写图形验证码" in sent[0][1]


def test_task_recovery_link_targets_jwxk_and_uses_generic_recovery_flow(tmp_path):
    starts = []
    accepted = []

    class FakeJwxkRecoveryClient:
        username = "20250001"

        def poll_webvpn_qr_login(self, flow_id):
            assert flow_id == "jwxk-recovery-flow"
            return {"status": "authenticated", "target_service": "jwxk"}

        def cancel_webvpn_qr_login(self, _flow_id):
            return None

    client = FakeJwxkRecoveryClient()

    def start_qr(target_service):
        starts.append(target_service)
        return client, {
            "flow_id": "jwxk-recovery-flow",
            "qr_content": "https://pass.neu.edu.cn/qr",
            "expires_in": 300,
        }

    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=FakeStorage(),
        logger=logging.getLogger("task-jwxk-recovery-test"),
        qr_login_starter=start_qr,
        auth_setter=lambda candidate, target: accepted.append((candidate, target)),
    )
    service.update_config(mail_config(site_url="https://toolkit.example.com"))
    assert service.get_status()["stage"] == "disabled"

    queued = service.request_auth_recovery_notification(
        subject="JWXK 自动任务登录失效",
        body="任务已暂停，不会重放写操作。",
        html_body="<html><body><p>任务已暂停</p></body></html>",
        dedupe_key="student:batch:task:auth-required:1",
        target_service="jwxk",
    )

    assert queued is True
    assert service.get_status()["stage"] == "disabled"
    assert len(service._outbox) == 1
    message = service._outbox[0]
    assert message["dedupe_key"].startswith("auth-recovery:course_selection:")
    assert "/auth-recovery/" not in message["body"]
    sent = []
    service._send_email = lambda _config, _subject, body, html_body="": sent.append((body, html_body))
    service._flush_outbox()
    token = re.search(
        r"/auth-recovery/([A-Za-z0-9_.-]+)", sent[0][0]
    ).group(1)
    assert "打开一次性登录页面" in sent[0][1]

    service.start_recovery_login(token)
    result = service.poll_recovery_login(token)

    assert starts == ["jwxk"]
    assert result["status"] == "authenticated"
    assert accepted == [(client, "jwxk")]
    assert service.get_status()["stage"] == "disabled"
    with pytest.raises(ValueError):
        service.get_recovery_status(token)


def test_task_recovery_without_site_url_queues_manual_login_notice(tmp_path):
    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=FakeStorage(),
        logger=logging.getLogger("task-manual-recovery-test"),
    )
    service.update_config(mail_config(site_url=""))

    assert service.request_auth_recovery_notification(
        subject="自动任务登录失效",
        body="任务已暂停。",
        dedupe_key="student:batch:task:auth-required:1",
        target_service="jwxk",
    ) is True

    assert len(service._outbox) == 1
    assert service._outbox[0]["kind"] == "auth_recovery"
    rendered = service.recovery_service.materialize_mail(service._outbox[0])
    assert "手动登录" in rendered["body"]
    assert "/grade-tracking/recovery/" not in service._outbox[0]["body"]
    assert not service._state.get("recovery_token_hash")


def test_pending_sms_recovery_must_match_requested_service(tmp_path):
    starts = []
    primary_challenge = {
        "status": "sms_required",
        "flow_id": "primary-sms",
        "captcha_image": "data:image/jpeg;base64,abc",
        "expires_in": 180,
        "target_service": "primary",
    }
    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=FakeStorage(),
        logger=logging.getLogger("task-target-isolation-test"),
        qr_login_starter=lambda target: starts.append(target),
        pending_sms_provider=lambda target: (object(), primary_challenge),
    )
    service.update_config(mail_config(site_url="https://toolkit.example.com"))

    service.request_auth_recovery_notification(
        subject="自动任务登录失效",
        body="任务已暂停。",
        dedupe_key="student:batch:task:auth-required:1",
        target_service="jwxk",
    )

    assert service._recovery_flow is None
    assert service._outbox[0]["template_metadata"]["continued_sms"] is False


def test_recovery_sms_captcha_error_keeps_flow_for_retry(tmp_path, monkeypatch):
    sent = []

    class FakeSMSClient:
        username = "20250001"

        def poll_webvpn_qr_login(self, _flow_id):
            return {
                "status": "sms_required",
                "flow_id": "sms-flow",
                "captcha_image": "data:image/jpeg;base64,first",
                "expires_in": 180,
            }

        def send_webvpn_sms_code(self, _flow_id, _captcha_code):
            return {
                "status": "captcha_invalid",
                "message": "图形验证码不正确",
                "captcha_image": "data:image/jpeg;base64,second",
            }

        def cancel_webvpn_qr_login(self, _flow_id):
            return None

        def cancel_webvpn_sms_login(self, _flow_id):
            return None

    service = GradeTrackingService(
        data_dir=tmp_path,
        auth_provider=lambda: None,
        score_storage=FakeStorage(),
        logger=logging.getLogger("grade-tracking-sms-retry-test"),
        qr_login_starter=lambda: (
            FakeSMSClient(),
            {"flow_id": "qr-flow", "qr_content": "qr", "expires_in": 300},
        ),
    )
    service.update_config(mail_config(site_url="https://grades.example.com"))
    monkeypatch.setattr(
        service,
        "_send_email",
        lambda config, subject, body: sent.append((subject, body)),
    )
    service.check_now()
    service._flush_outbox()
    token = re.search(
        r"/auth-recovery/([A-Za-z0-9_.-]+)", sent[0][1]
    ).group(1)
    service.start_recovery_login(token)
    service.poll_recovery_login(token)

    result = service.send_recovery_sms(token, "bad")

    assert result["status"] == "captcha_invalid"
    status = service.get_recovery_status(token)
    assert status["status"] == "sms_required"
    assert status["captcha_image"].endswith("second")


def test_recovery_api_bypasses_server_password_only_with_token_path():
    assert not is_public_api_path(
        "/api/grade-tracking/recovery/token-value/start"
    )
    assert is_public_api_path(
        "/api/auth-recovery/context.signature/start"
    )
    assert not is_public_api_path("/api/grade-tracking/config")
    assert redact_sensitive_path(
        "/api/auth-recovery/context.signature/poll"
    ) == "/api/auth-recovery/<redacted>/poll"
    assert redact_sensitive_path(
        "/api/auth-recovery/context.signature/sms/verify"
    ) == "/api/auth-recovery/<redacted>/sms/verify"


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
