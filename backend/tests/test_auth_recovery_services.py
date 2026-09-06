import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from backend.core.auth.recovery import MAX_STORED_CONTEXTS, RemoteAuthRecoveryService
from backend.core.notifications import SystemMailService
from backend.core.storage import Storage, StorageConfig
from backend.core.tracking import GradeTrackingService


LOGGER = logging.getLogger("auth-recovery-services-test")


def configure_mail(service):
    service.update_config({
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "smtp_username": "sender@example.com",
        "smtp_password": "secret",
        "from_email": "sender@example.com",
        "to_email": "receiver@example.com",
    })


def build_recovery(tmp_path, **kwargs):
    mail = SystemMailService(tmp_path, LOGGER)
    configure_mail(mail)
    recovery = RemoteAuthRecoveryService(
        tmp_path, mail_service=mail, logger=LOGGER, **kwargs,
    )
    recovery.update_config({"public_base_url": "https://toolkit.example.com"})
    return mail, recovery


def active_token(recovery):
    context = next(item for item in recovery._contexts.values() if item["status"] == "active")
    return recovery._token(context)


def test_successful_sms_commit_can_invalidate_mail_flows_without_reentering_remote_guard(tmp_path):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from unittest.mock import Mock

    held = False

    @contextmanager
    def exclusive():
        nonlocal held
        assert not held, "non-reentrant remote guard entered during successful login"
        held = True
        try:
            yield
        finally:
            held = False

    client = SimpleNamespace(
        username="student",
        verify_webvpn_sms_code=Mock(return_value={"status": "authenticated"}),
        cancel_webvpn_sms_login=Mock(),
    )
    _, recovery = build_recovery(tmp_path, remote_guard=exclusive)
    recovery.request_notification(
        source="grade_tracking", target_service="primary", account_id="student",
        subject="登录失效", body="请恢复", dedupe_key="episode",
    )
    token = active_token(recovery)
    context_id = token.split(".")[0]
    recovery._flows[context_id] = {"client": client, "flow": {"kind": "sms", "flow_id": "sms"}}

    def commit(_client):
        assert held, "identity commit must remain inside the exclusive boundary"
        recovery.invalidate_all()

    recovery.auth_committer = commit
    result = recovery.verify_sms(token, "654321")
    assert result["status"] == "authenticated"
    assert recovery._contexts[context_id]["status"] == "completed"
    assert not held
    # A subsequent timetable request can acquire the same boundary.
    with exclusive():
        pass


def test_foreground_login_cleanup_does_not_take_remote_guard_again(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock

    def forbidden():
        raise AssertionError("login already holds the remote guard")

    _, recovery = build_recovery(tmp_path, remote_guard=forbidden)
    recovery.request_notification(
        source="grade_tracking", target_service="primary", account_id="student",
        subject="登录失效", body="请恢复", dedupe_key="episode",
    )
    token = active_token(recovery)
    client = SimpleNamespace(cancel_webvpn_sms_login=Mock())
    recovery._flows[token.split(".")[0]] = {
        "client": client, "flow": {"kind": "sms", "flow_id": "sms"},
    }
    recovery.invalidate_all()
    client.cancel_webvpn_sms_login.assert_called_once_with("sms")


def test_recovery_waits_for_remote_slot_without_holding_local_context_lock(tmp_path):
    from contextlib import contextmanager
    from threading import Event, Thread

    queued, release, finished = Event(), Event(), Event()
    errors = []

    @contextmanager
    def guard():
        queued.set()
        assert release.wait(2)
        yield

    _, recovery = build_recovery(
        tmp_path, remote_guard=guard,
        qr_login_starter=lambda: (object(), {"flow_id": "qr"}),
    )
    recovery.request_notification(
        source="grade_tracking", target_service="primary", account_id="student",
        subject="登录失效", body="请恢复", dedupe_key="episode",
    )
    token = active_token(recovery)

    def worker():
        try:
            recovery.start(token)
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()

    thread = Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert queued.wait(1)
        acquired = recovery._lock.acquire(timeout=0.5)
        assert acquired, "recovery lock must remain available to a foreground login commit"
        recovery._lock.release()
    finally:
        release.set()
        thread.join(3)
    assert finished.is_set()
    assert not errors


def test_reopening_adopted_sms_keeps_live_flow_but_stale_flow_keeps_token_valid(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from backend.core.auth.client import WebVPNLoginError, WEBVPN_ERR_FLOW_MISSING

    challenge = {"status": "sms_required", "flow_id": "sms", "expires_in": 300}
    client = SimpleNamespace(
        get_webvpn_sms_challenge=Mock(return_value=challenge),
        cancel_webvpn_sms_login=Mock(),
    )
    _, recovery = build_recovery(
        tmp_path, pending_sms_provider=lambda: (client, challenge),
    )
    recovery.request_notification(
        source="grade_tracking", target_service="primary", account_id="student",
        subject="登录失效", body="请恢复", dedupe_key="episode",
    )
    token = active_token(recovery)
    assert recovery.start(token)["flow_id"] == "sms"
    assert recovery.start(token)["flow_id"] == "sms"
    client.cancel_webvpn_sms_login.assert_not_called()
    client.get_webvpn_sms_challenge.return_value = None
    with pytest.raises(WebVPNLoginError) as error:
        recovery.send_sms(token, "1234")
    assert error.value.error_code == WEBVPN_ERR_FLOW_MISSING
    assert recovery.get_status(token) == {"status": "ready"}


def test_tracking_service_contains_no_mail_or_recovery_api(tmp_path):
    mail, recovery = build_recovery(tmp_path)
    tracker = GradeTrackingService(
        tmp_path,
        auth_provider=lambda: None,
        score_storage=object(),
        logger=LOGGER,
        mail_service=mail,
        auth_recovery_service=recovery,
    )

    for name in (
        "test_email", "queue_system_notification", "request_auth_recovery_notification",
        "start_recovery_login", "poll_recovery_login", "send_recovery_sms",
        "verify_recovery_sms", "get_recovery_status",
    ):
        assert not hasattr(tracker, name)


def test_recovery_outbox_and_state_never_persist_plaintext_token(tmp_path):
    mail, recovery = build_recovery(tmp_path)
    assert recovery.request_notification(
        source="grade_tracking",
        target_service="primary",
        account_id="student",
        subject="登录失效",
        body="请恢复登录",
        dedupe_key="episode-1",
    )
    token = active_token(recovery)
    state_text = recovery.state_path.read_text(encoding="utf-8")
    outbox_text = mail.outbox_path.read_text(encoding="utf-8")

    assert token not in state_text
    assert token not in outbox_text
    assert "/auth-recovery/" not in outbox_text
    assert "token_hash" in state_text
    assert "context_id" in outbox_text


def test_recovery_link_survives_restart_without_persisting_flow(tmp_path):
    mail, recovery = build_recovery(tmp_path)
    recovery.request_notification(
        source="grade_tracking", target_service="primary", account_id="student",
        subject="登录失效", body="请恢复登录", dedupe_key="episode-1",
    )
    token = active_token(recovery)
    restarted_mail = SystemMailService(tmp_path, LOGGER)
    restarted = RemoteAuthRecoveryService(
        tmp_path, mail_service=restarted_mail, logger=LOGGER,
    )

    assert restarted.get_status(token) == {"status": "ready"}
    assert restarted._flows == {}


def test_qr_sms_flow_and_recovered_callback(tmp_path):
    committed = []
    resumed = []

    class Client:
        username = "student"

        def poll_webvpn_qr_login(self, _flow_id):
            return {
                "status": "sms_required", "flow_id": "sms-flow",
                "captcha_image": "data:image/jpeg;base64,abc", "expires_in": 180,
            }

        def send_webvpn_sms_code(self, _flow_id, _captcha):
            return {"status": "sent", "expires_in": 300}

        def verify_webvpn_sms_code(self, _flow_id, _code, _trust=False):
            return {"status": "authenticated"}

        def cancel_webvpn_qr_login(self, _flow_id):
            return None

        def cancel_webvpn_sms_login(self, _flow_id):
            return None

    client = Client()
    mail, recovery = build_recovery(
        tmp_path,
        qr_login_starter=lambda: (client, {"flow_id": "qr-flow", "qr_content": "qr", "expires_in": 300}),
        auth_committer=committed.append,
    )
    recovery.register_recovered_callback("grade_tracking", resumed.append)
    recovery.request_notification(
        source="grade_tracking", target_service="primary", account_id="student",
        subject="登录失效", body="请恢复登录", dedupe_key="episode-1",
    )
    token = active_token(recovery)

    assert recovery.start(token)["status"] == "pending"
    assert recovery.poll(token)["status"] == "sms_required"
    assert recovery.send_sms(token, "1234")["status"] == "sent"
    assert recovery.verify_sms(token, "654321")["status"] == "authenticated"
    assert committed == [client]
    assert resumed == ["student"]
    with pytest.raises(ValueError):
        recovery.get_status(token)


def test_legacy_config_and_outbox_migrate_once(tmp_path):
    (tmp_path / "grade_tracking_config.json").write_text(json.dumps({
        "enabled": True,
        "interval_minutes": 45,
        "start_hour": 8,
        "end_hour": 22,
        "site_url": "https://toolkit.example.com",
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "smtp_username": "sender@example.com",
        "smtp_password": "secret",
        "from_email": "sender@example.com",
        "to_email": "receiver@example.com",
    }), encoding="utf-8")
    (tmp_path / "grade_tracking_outbox.json").write_text(json.dumps({"messages": [
        {"id": "normal", "dedupe_key": "revision:1", "subject": "成绩", "body": "变化"},
        {"id": "old-recovery", "dedupe_key": "recovery-link:old", "subject": "恢复", "body": "/grade-tracking/recovery/secret"},
    ]}), encoding="utf-8")

    mail = SystemMailService(tmp_path, LOGGER)
    recovery = RemoteAuthRecoveryService(tmp_path, mail_service=mail, logger=LOGGER)
    GradeTrackingService(
        tmp_path, auth_provider=lambda: None, score_storage=object(), logger=LOGGER,
        mail_service=mail, auth_recovery_service=recovery,
    )

    assert mail.get_config()["smtp_password_configured"] is True
    assert recovery.get_config()["public_base_url"] == "https://toolkit.example.com"
    assert [item["id"] for item in mail._outbox] == ["normal"]
    cleaned = json.loads((tmp_path / "grade_tracking_config.json").read_text(encoding="utf-8"))
    assert set(cleaned) <= {"enabled", "interval_minutes", "start_hour", "end_hour", "_activation_id", "_activation_delivered_id"}

    restarted_mail = SystemMailService(tmp_path, LOGGER)
    assert [item["id"] for item in restarted_mail._outbox] == ["normal"]


def test_system_mail_counts_and_discards_each_business_source_independently(tmp_path):
    mail = SystemMailService(tmp_path, LOGGER)
    configure_mail(mail)
    assert mail.queue_notification("grade_tracking", "成绩", "变化", "grade:1")
    assert mail.queue_notification("course_selection", "选课", "结果", "selection:1")
    assert mail.queue_notification(
        "course_selection", "恢复", "模板", "auth:1",
        kind="auth_recovery", template_metadata={"context_id": "context"},
    )

    assert mail.pending_count() == 3
    assert mail.pending_count("grade_tracking") == 1
    assert mail.pending_count("course_selection") == 2

    mail.discard(kind="auth_recovery")
    assert mail.pending_count("grade_tracking") == 1
    assert mail.pending_count("course_selection") == 1


def test_terminal_recovery_context_history_is_bounded_without_pruning_active_links(tmp_path):
    _mail, recovery = build_recovery(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    current = datetime.now(timezone.utc).isoformat()
    recovery._contexts = {
        **{
            f"{index:032x}": {
                "context_id": f"{index:032x}",
                "status": "completed",
                "created_at": old if index < 10 else current,
                "completed_at": old if index < 10 else current,
            }
            for index in range(MAX_STORED_CONTEXTS + 30)
        },
        "f" * 32: {
            "context_id": "f" * 32,
            "status": "active",
            "created_at": old,
            "token_hash": "hash",
        },
    }

    with recovery._lock:
        recovery._save_state_locked()

    assert len(recovery._contexts) == MAX_STORED_CONTEXTS
    assert recovery._contexts["f" * 32]["status"] == "active"
    assert not any(
        context.get("status") == "completed" and context.get("completed_at") == old
        for context in recovery._contexts.values()
    )


def test_existing_adopted_sms_flow_is_reused_for_duplicate_notification(tmp_path):
    calls = []
    client = object()
    flow = {
        "status": "sms_required",
        "flow_id": "sms-flow",
        "captcha_image": "data:image/jpeg;base64,abc",
        "target_service": "jwxk",
    }
    mail, recovery = build_recovery(
        tmp_path,
        pending_sms_provider=lambda target: calls.append(target) or (client, flow),
    )
    assert recovery.request_notification(
        source="course_selection", target_service="jwxk", account_id="student",
        subject="登录失效", body="请恢复登录", dedupe_key="episode-1",
    )
    context = next(iter(recovery._contexts.values()))
    assert recovery._adopt_pending_sms(context["context_id"], "jwxk") is True
    assert calls == ["jwxk"]
    assert mail.pending_count("course_selection") == 1


def test_logout_style_invalidation_discards_link_and_manual_recovery_mail_only(tmp_path):
    mail = SystemMailService(tmp_path, LOGGER)
    configure_mail(mail)
    recovery = RemoteAuthRecoveryService(tmp_path, mail_service=mail, logger=LOGGER)
    assert recovery.request_notification(
        source="grade_tracking", target_service="primary", account_id="student",
        subject="登录失效", body="请恢复登录", dedupe_key="episode-1",
    )
    assert mail.queue_notification(
        "grade_tracking", "成绩变化", "课程成绩已更新", "revision:1",
    )

    recovery.invalidate_all()

    assert mail.pending_count("grade_tracking") == 1
    assert mail._outbox[0]["dedupe_key"] == "revision:1"
    assert all(context["status"] == "invalidated" for context in recovery._contexts.values())


def test_source_scoped_invalidation_does_not_cancel_other_business_recovery(tmp_path):
    mail, recovery = build_recovery(tmp_path)
    assert recovery.request_notification(
        source="grade_tracking", target_service="primary", account_id="student",
        subject="成绩登录失效", body="请恢复登录", dedupe_key="grade-episode",
    )
    assert recovery.request_notification(
        source="course_selection", target_service="jwxk", account_id="student",
        subject="选课登录失效", body="请恢复登录", dedupe_key="selection-episode",
    )

    assert recovery.invalidate_matching(
        source="grade_tracking", target_service="primary", account_id="student",
    ) == 1

    contexts = {item["source"]: item for item in recovery._contexts.values()}
    assert contexts["grade_tracking"]["status"] == "invalidated"
    assert contexts["course_selection"]["status"] == "active"
    assert mail.pending_count("grade_tracking") == 0
    assert mail.pending_count("course_selection") == 1


def test_clear_personal_data_preserves_shared_mail_and_recovery_configuration(tmp_path):
    preserved = {
        "system_mail_config.json": "{}",
        "auth_recovery_config.json": "{}",
        "auth_recovery_secret.key": "signing-secret",
    }
    for filename, content in preserved.items():
        (tmp_path / filename).write_text(content, encoding="utf-8")
    (tmp_path / "system_mail_outbox.json").write_text("{}", encoding="utf-8")
    (tmp_path / "auth_recovery_state.json").write_text("{}", encoding="utf-8")

    Storage(StorageConfig(data_dir=str(tmp_path))).clear_all_data(preserve_config=True)

    assert all((tmp_path / filename).exists() for filename in preserved)
    assert not (tmp_path / "system_mail_outbox.json").exists()
    assert not (tmp_path / "auth_recovery_state.json").exists()
