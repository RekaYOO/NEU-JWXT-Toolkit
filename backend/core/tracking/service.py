"""Lightweight grade-change tracking built on the shared auth and score APIs."""

from __future__ import annotations

import json
import hashlib
import secrets
import smtplib
import ssl
import threading
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any, Callable


from backend.core.runtime.config import secure_file
from backend.core.cache.resources import SCORE_FIELDS, diff_scores, score_key


CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "interval_minutes": 30,
    "start_hour": 9,
    "end_hour": 21,
    "site_url": "",
    "smtp_host": "",
    "smtp_port": 465,
    "smtp_security": "ssl",
    "smtp_username": "",
    "smtp_password": "",
    "from_email": "",
    "to_email": "",
}
DEFAULT_STATE: dict[str, Any] = {
    "stage": "disabled",
    "message": "成绩追踪未启用",
    "last_check_at": None,
    "last_success_at": None,
    "next_check_at": None,
    "last_notification_at": None,
    "last_error": None,
}


def _now() -> datetime:
    return datetime.now(CHINA_TZ)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


class GradeTrackingService:
    """Owns persisted tracking state and one daemon scheduler thread."""

    def __init__(
        self,
        data_dir: str | Path,
        auth_provider: Callable[[], Any],
        score_storage: Any,
        logger: Any,
        report_storage: Any | None = None,
        qr_login_starter: Callable[[], tuple[Any, dict[str, Any]]] | None = None,
        auth_setter: Callable[[Any], None] | None = None,
        login_flow_pending: Callable[[], bool] | None = None,
        pending_sms_provider: Callable[[], tuple[Any, dict[str, Any]] | None] | None = None,
        score_refresher: Callable[[str, bool], dict[str, Any]] | None = None,
        score_detail_lookup: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        remote_guard: Callable[[], Any] | None = None,
        auth_error_code_provider: Callable[[], str] | None = None,
    ) -> None:
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.config_path = root / "grade_tracking_config.json"
        self.snapshot_path = root / "grade_tracking_snapshot.json"
        self.state_path = root / "grade_tracking_state.json"
        self.outbox_path = root / "grade_tracking_outbox.json"
        self.auth_provider = auth_provider
        self.score_storage = score_storage
        self.report_storage = report_storage
        self.logger = logger
        self.qr_login_starter = qr_login_starter
        self.auth_setter = auth_setter
        self.login_flow_pending = login_flow_pending
        self.pending_sms_provider = pending_sms_provider
        self.score_refresher = score_refresher
        self.score_detail_lookup = score_detail_lookup
        self.remote_guard = remote_guard or nullcontext
        self.auth_error_code_provider = auth_error_code_provider
        self._lock = threading.RLock()
        self._check_lock = threading.Lock()
        self._revision_lock = threading.RLock()
        self._recovery_lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._recovery_client: Any | None = None
        self._recovery_flow: dict[str, Any] | None = None
        self._config = {**DEFAULT_CONFIG, **self._read_json(self.config_path, {})}
        self._config.pop("notify_initial", None)
        try:
            interval = int(self._config.get("interval_minutes", 30))
        except (TypeError, ValueError):
            interval = int(DEFAULT_CONFIG["interval_minutes"])
        self._config["interval_minutes"] = min(1440, max(5, interval))
        self._state = {**DEFAULT_STATE, **self._read_json(self.state_path, {})}
        messages = self._read_json(
            self.outbox_path,
            {"messages": []},
        ).get("messages", [])
        self._outbox = list(messages) if isinstance(messages, list) else []
        # Keep the pending activation intent beside ``enabled`` so one atomic
        # config write records the complete switch transition.  Migrate the
        # earlier state marker (or a queued activation message) in memory.
        legacy_activation_id = str(
            self._state.pop("initial_notification_id", None) or ""
        )
        queued_activation_id = next(
            (
                str(item.get("dedupe_key") or "").removeprefix("activation:")
                for item in self._outbox
                if str(item.get("dedupe_key") or "").startswith("activation:")
            ),
            "",
        )
        if (
            queued_activation_id
            and queued_activation_id
            == str(self._config.get("_activation_delivered_id") or "")
        ):
            queued_activation_id = ""
        if self._config.get("enabled") and not self._config.get("_activation_id"):
            activation_id = legacy_activation_id or queued_activation_id
            if activation_id:
                self._config["_activation_id"] = activation_id
        if (
            self._config.get("enabled")
            and self._config.get("_activation_id")
            and self._state.get("stage") == "disabled"
        ):
            self._state.update(
                stage="scheduled",
                message="成绩追踪已启用，正在准备初始邮件",
                next_check_at=_iso(),
                last_error=None,
            )

    @staticmethod
    def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            if path.exists():
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    return value
        except (OSError, ValueError, TypeError):
            pass
        return fallback.copy()

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
        secure_file(path)

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._scheduler,
                name="grade-tracking",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._recovery_lock:
            self._cancel_recovery_flow_locked()
            self._recovery_client = None
            self._recovery_flow = None
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            result = {
                key: value
                for key, value in self._config.items()
                if key != "smtp_password" and not key.startswith("_")
            }
            result["smtp_password_configured"] = bool(self._config.get("smtp_password"))
            return result

    def get_mail_status(self) -> dict[str, Any]:
        """Expose only whether the shared SMTP channel is usable."""
        with self._lock:
            configured = bool(
                self._config.get("smtp_host") and self._config.get("from_email")
                and self._config.get("to_email")
                and (self._config.get("smtp_password") or self._config.get("smtp_username"))
            )
            return {"configured": configured, "status": "邮件通道可用" if configured else "请前往系统设置配置邮件"}

    def queue_system_notification(
        self, subject: str, body: str, dedupe_key: str, html_body: str = "",
    ) -> bool:
        """Queue a non-grade notification in the existing durable SMTP outbox."""
        if not self.get_mail_status()["configured"]:
            return False
        self._queue_email(
            subject, body, f"course-selection:{dedupe_key}", html_body=html_body,
        )
        self._wake.set()
        return True

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._state,
                "enabled": bool(self._config.get("enabled")),
                "pending_notifications": len(self._outbox),
            }

    def update_config(self, values: dict[str, Any]) -> dict[str, Any]:
        incoming = dict(values)
        # Removed setting: every disabled -> enabled transition now schedules
        # one initial notification unconditionally.
        incoming.pop("notify_initial", None)
        incoming.pop("_activation_id", None)
        with self._lock:
            previous_site_url = str(self._config.get("site_url", "")).strip()
            previously_enabled = bool(self._config.get("enabled"))
            candidate = self._config.copy()
            password = incoming.pop("smtp_password", None)
            clear_password = incoming.pop("clear_smtp_password", False)
            candidate.update(incoming)
            if password:
                candidate["smtp_password"] = password
            elif clear_password:
                candidate["smtp_password"] = ""
            if candidate.get("enabled") and not previously_enabled:
                candidate["_activation_id"] = str(uuid.uuid4())
            elif not candidate.get("enabled"):
                candidate.pop("_activation_id", None)
            self._validate_config(candidate, require_complete=bool(candidate["enabled"]))
            site_url_changed = (
                str(candidate.get("site_url", "")).strip() != previous_site_url
            )
            self._write_json(self.config_path, candidate)
            self._config = candidate
            if self._config["enabled"]:
                if not previously_enabled:
                    self._outbox = [
                        item
                        for item in self._outbox
                        if not str(item.get("dedupe_key") or "").startswith(
                            "activation:"
                        )
                    ]
                    self._save_outbox()
                # Config saves and idempotent PATCH true must preserve
                # waiting_login/monitoring.  ``disabled`` is also accepted as
                # recovery from a prior auxiliary state-file write failure.
                if not previously_enabled or self._state.get("stage") == "disabled":
                    self._state.update(
                        stage="scheduled",
                        message="成绩追踪已启用，正在准备初始邮件",
                        next_check_at=_iso(),
                        last_error=None,
                    )
            else:
                self._outbox = [
                    item
                    for item in self._outbox
                    if not str(item.get("dedupe_key") or "").startswith(
                        "activation:"
                    )
                ]
                self._save_outbox()
                self._state.update(
                    stage="disabled",
                    message="成绩追踪未启用",
                    next_check_at=None,
                )
            self._save_state()
        if site_url_changed:
            self._invalidate_recovery_link()
        self._wake.set()
        return self.get_config()

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        """Persist and apply the tracking switch without changing form fields."""
        return self.update_config({"enabled": bool(enabled)})

    @staticmethod
    def _validate_config(config: dict[str, Any], require_complete: bool) -> None:
        interval = int(config.get("interval_minutes", 0))
        start = int(config.get("start_hour", -1))
        end = int(config.get("end_hour", -1))
        if not 5 <= interval <= 1440:
            raise ValueError("检查间隔必须在 5–1440 分钟之间")
        if not 0 <= start <= 23 or not 1 <= end <= 24 or start >= end:
            raise ValueError("每日检查时段必须是有效且递增的整点范围")
        if config.get("smtp_security") not in {"ssl", "starttls", "none"}:
            raise ValueError("不支持的 SMTP 安全方式")
        if not 1 <= int(config.get("smtp_port", 0)) <= 65535:
            raise ValueError("SMTP 端口无效")
        if require_complete:
            missing = [
                label
                for key, label in (
                    ("smtp_host", "SMTP 服务器"),
                    ("from_email", "发件地址"),
                    ("to_email", "收件地址"),
                )
                if not str(config.get(key, "")).strip()
            ]
            if config.get("smtp_username") and not config.get("smtp_password"):
                missing.append("SMTP 密码")
            if missing:
                raise ValueError("启用前请填写：" + "、".join(missing))

    def test_email(self) -> None:
        with self._lock:
            config = self._config.copy()
        self._validate_config(config, require_complete=True)
        self._send_email(
            config,
            "[NEU 教务工具箱] 系统邮件配置测试",
            "这是一封系统邮件配置测试邮件。\n\n如果你能收到这封邮件，说明当前 SMTP 服务器、端口、安全方式和账号配置可以正常发送邮件。\n\n此测试不代表任何具体业务功能，仅用于验证系统邮件通道。",
        )

    def check_now(self) -> dict[str, Any]:
        return self._run_check(manual=True)

    def pause_for_logout(self, *, clear_personal_state: bool = False) -> None:
        """Pause execution while preserving the user's enabled intent/config."""
        with self._lock:
            self._state.update(
                stage="paused_logout",
                message="教务登录已退出，重新登录后将自动恢复成绩追踪",
                next_check_at=None,
                last_error=None,
            )
            if clear_personal_state:
                for path in (self.snapshot_path, self.state_path, self.outbox_path):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                self._outbox = []
                self._state = {
                    **DEFAULT_STATE,
                    "stage": "paused_logout",
                    "message": "教务登录已退出，重新登录后将自动恢复成绩追踪",
                }
            self._save_state()
        self._invalidate_recovery_link()
        self._wake.set()

    def resume_after_login(self, account_id: str) -> None:
        with self._lock:
            previous_account = str(self._state.get("account_id") or "")
            has_unscoped_personal_state = bool(
                not previous_account
                and (
                    self.snapshot_path.exists()
                    or self._outbox
                    or self._state.get("last_seen_revision")
                    or self._state.get("last_notified_revision")
                )
            )
            if has_unscoped_personal_state or (
                previous_account and previous_account != str(account_id)
            ):
                try:
                    self.snapshot_path.unlink(missing_ok=True)
                except OSError:
                    pass
                self._outbox = []
                self._write_json(self.outbox_path, {"messages": []})
                self._state = {
                    **DEFAULT_STATE,
                    "account_id": str(account_id),
                }
            else:
                self._state["account_id"] = str(account_id)
            self._state.pop("manual_login_notice_sent", None)
            self._state.pop("manual_login_notice_at", None)
            self._outbox = [
                item
                for item in self._outbox
                if not str(item.get("dedupe_key") or "").startswith(
                    "login-required:"
                )
            ]
            self._save_outbox()
            if not self._config.get("enabled"):
                self._save_state()
                return
            self._state.update(
                stage="scheduled",
                message="登录已恢复，等待检查成绩",
                next_check_at=_iso(),
                last_error=None,
            )
            self._save_state()
        self._wake.set()

    def _scheduler(self) -> None:
        while not self._stop.is_set():
            try:
                with self._lock:
                    activation_at_head = bool(
                        self._outbox
                        and str(self._outbox[0].get("dedupe_key") or "").startswith(
                            "activation:"
                        )
                    )
                    may_notify = bool(
                        self._outbox and (
                            self._config.get("enabled")
                            and (self._within_window(_now()) or activation_at_head)
                            or str(self._outbox[0].get("dedupe_key") or "").startswith("course-selection:")
                        )
                    )
                if may_notify:
                    self._flush_outbox()
                if self._should_run():
                    self._run_check(manual=False)
            except Exception as error:
                self.logger.exception("[成绩追踪] 调度失败")
                self._record_error(f"{type(error).__name__}: {error}")
            self._wake.wait(20)
            self._wake.clear()

    def _should_run(self) -> bool:
        with self._lock:
            if not self._config.get("enabled"):
                return False
            activation_key = f"activation:{self._config.get('_activation_id', '')}"
            activation_already_queued = any(
                str(item.get("dedupe_key") or "") == activation_key
                for item in self._outbox
            )
            if (
                self._config.get("_activation_id")
                and not activation_already_queued
                and self._state.get("stage") == "scheduled"
            ):
                return True
            now = _now()
            if not self._within_window(now):
                self._state.update(
                    stage="outside_window",
                    message="当前不在设定的检查时段",
                    next_check_at=self._next_window_start(now).isoformat(),
                )
                self._save_state()
                return False
            next_text = self._state.get("next_check_at")
            if not next_text:
                return True
            try:
                return now >= datetime.fromisoformat(next_text)
            except ValueError:
                return True

    def _within_window(self, now: datetime) -> bool:
        return int(self._config["start_hour"]) <= now.hour < int(self._config["end_hour"])

    def _next_window_start(self, now: datetime) -> datetime:
        start = now.replace(
            hour=int(self._config["start_hour"]),
            minute=0,
            second=0,
            microsecond=0,
        )
        return start if now < start else start + timedelta(days=1)

    def _run_check(self, manual: bool) -> dict[str, Any]:
        if not self._check_lock.acquire(blocking=False):
            raise RuntimeError("已有成绩检查正在进行")
        try:
            with self._lock:
                config = self._config.copy()
                if not config.get("enabled") and not manual:
                    return self.get_status()
                self._state.update(
                    stage="checking",
                    message="正在检查最新成绩",
                    last_check_at=_iso(),
                    last_error=None,
                )
                self._save_state()

            auth = self.auth_provider()
            if auth is None:
                if self.login_flow_pending and self.login_flow_pending():
                    with self._lock:
                        self._state.update(
                            stage="waiting_login",
                            message="WebVPN 需要交互式验证，正在通知用户恢复登录",
                            next_check_at=(
                                _now() + timedelta(minutes=int(config["interval_minutes"]))
                            ).isoformat(),
                        )
                        self._save_state()
                auth = self._handle_login_required(config)
                if auth is None:
                    return self.get_status()
            else:
                self._invalidate_recovery_link()
            self.resume_after_login(str(auth.username))

            if not self.score_refresher:
                raise RuntimeError("成绩追踪尚未接入统一成绩资源")
            result = self.score_refresher(str(auth.username), manual)
            payload = result.get("payload") or {}
            revision = str(result.get("revision") or "")
            scores = payload.get("scores") or []
            overall_gpa = payload.get("overall_gpa")
            notification_result = self.handle_scores_revision(
                str(auth.username),
                revision,
                payload,
                reason="tracking",
            )

            with self._lock:
                effective_change_count = notification_result.get("change_count")
                if not effective_change_count:
                    effective_change_count = int(
                        self._state.get("last_change_count") or 0
                    )
                self._state.update(
                    stage="monitoring",
                    message="tracking check completed",
                    last_success_at=_iso(),
                    next_check_at=(
                        _now() + timedelta(minutes=int(config["interval_minutes"]))
                    ).isoformat(),
                    last_error=None,
                    course_count=len(scores),
                    overall_gpa=overall_gpa,
                    last_change_count=effective_change_count,
                )
                self._save_state()
            return {
                **self.get_status(),
                "revision": revision,
                "additions": notification_result.get("additions", []),
                "changes": notification_result.get("changes", []),
                "removals": notification_result.get("removals", []),
            }
        except Exception as error:
            self.logger.exception("[成绩追踪] 检查失败")
            self._record_error(f"{type(error).__name__}: {error}")
            if manual:
                raise
            return self.get_status()
        finally:
            self._check_lock.release()

    def _handle_login_required(self, config: dict[str, Any]) -> Any | None:
        with self._lock:
            self._state.update(
                stage="waiting_login",
                message="教务登录已失效，请重新登录后继续追踪",
                next_check_at=(
                    _now() + timedelta(minutes=int(config["interval_minutes"]))
                ).isoformat(),
            )
            self._save_state()
        link = str(config.get("site_url", "")).strip()
        # A campus-network WebVPN rejection is a routing problem, not a
        # CAPTCHA challenge.  Do not issue a QR/SMS recovery link that cannot
        # work on the current network; the next email tells the user to switch
        # the application back to direct campus access.
        auth_error_code = ""
        if self.auth_error_code_provider is not None:
            try:
                auth_error_code = str(self.auth_error_code_provider() or "")
            except Exception:
                auth_error_code = ""
        if auth_error_code == "WEBVPN_CAMPUS_NETWORK_BLOCKED":
            self._email_manual_login_notice(config, campus_network=True)
            return None
        if link:
            self._issue_recovery_link(link)
            return None
        self._email_manual_login_notice(config)
        return None

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _recovery_token_is_valid(self, token: str) -> bool:
        expected = str(self._state.get("recovery_token_hash") or "")
        return bool(
            expected
            and token
            and secrets.compare_digest(expected, self._token_hash(token))
        )

    def _issue_recovery_link(self, site_url: str) -> None:
        existing_token = False
        with self._lock:
            if self._state.get("recovery_token_hash"):
                self._state.update(
                    stage="waiting_login",
                    message="等待用户打开邮件中的一次性登录链接",
                )
                self._save_state()
                existing_token = True
            else:
                token = secrets.token_urlsafe(32)
                self._state.update(
                    recovery_token_hash=self._token_hash(token),
                    recovery_token_issued_at=_iso(),
                    stage="waiting_login",
                    message="等待用户打开邮件中的一次性登录链接",
                    last_login_notice_at=time.time(),
                )
                self._save_state()
        continued_sms = self._adopt_pending_sms_recovery()
        if existing_token:
            return
        recovery_url = (
            f"{site_url.rstrip('/')}/grade-tracking/recovery/{token}"
        )
        recovery_intro = (
            "后台已使用保存的账号信息完成第一步登录，请打开下面的一次性页面，"
            "直接填写图形验证码并获取短信验证码：\n"
            if continued_sms
            else "请打开下面的一次性登录页面重新登录；页面会先提供微信扫码，"
            "如果学校要求二次认证，会继续显示图形验证码、短信发送按钮和短信验证码输入框：\n"
        )
        self._queue_email(
            "[NEU 成绩追踪] 请打开一次性链接恢复登录",
            "成绩追踪无法访问教务系统。\n\n"
            + recovery_intro
            + f"{recovery_url}\n\n"
            "网页链接会在成功建立教务会话后失效。二维码或短信流程过期后，"
            "可在同一网页链接中重新开始登录。验证码不会由后台自动填写或发送。"
            "请勿转发此链接。",
            f"recovery-link:{self._token_hash(token)}",
        )
        self._flush_outbox()

    def _adopt_pending_sms_recovery(self) -> bool:
        """Continue a saved-credential SMS challenge without forcing another QR."""
        if not self.pending_sms_provider:
            return False
        with self._recovery_lock:
            if self._recovery_client and self._recovery_flow:
                return self._recovery_flow.get("kind") == "sms"
            candidate = self.pending_sms_provider()
            if not candidate:
                return False
            client, flow = candidate
            if not client or not flow or flow.get("status") != "sms_required":
                return False
            self._recovery_client = client
            self._recovery_flow = self._recovery_flow_payload("sms", flow)
            with self._lock:
                self._state.update(
                    stage="waiting_sms",
                    message="已续接后台登录，等待图形验证码和短信验证",
                )
                self._save_state()
            return True

    def _email_manual_login_notice(self, config: dict[str, Any], campus_network: bool = False) -> None:
        """Notify once when interactive recovery cannot be exposed safely."""
        with self._lock:
            if self._state.get("manual_login_notice_sent"):
                self._state.update(
                    stage="waiting_login",
                    message="登录已失效，请重新进入系统完成登录",
                )
                self._save_state()
                return
            notice_id = secrets.token_hex(12)
            self._state.update(
                manual_login_notice_sent=notice_id,
                manual_login_notice_at=_iso(),
                stage="waiting_login",
                message="登录失效通知已发送，请重新进入系统完成登录",
            )
            self._save_state()
        body = (
            "成绩追踪当前使用 WebVPN，但学校网关检测到校园网环境并拒绝了 WebVPN 请求。"
            "请重新进入 NEU 教务工具箱，将访问方式切换为“校内直连”后完成登录；登录成功后，"
            "成绩追踪会自动恢复。"
            if campus_network else
            "成绩追踪无法访问教务系统。\n\n"
            "当前 WebVPN 重新登录可能需要图形验证码和短信验证，邮件本身无法安全完成此步骤。"
            "请重新进入 NEU 教务工具箱并手动登录；登录成功后，成绩追踪会自动恢复。\n\n"
            "若希望以后直接从邮件完成恢复，请在成绩追踪设置中配置可访问的“重新登录地址”。"
        )
        self._queue_email(
            "[NEU 成绩追踪] 登录已失效",
            body,
            f"login-required:{notice_id}",
        )
        self._wake.set()

    def _cancel_recovery_flow_locked(self) -> None:
        if not self._recovery_client or not self._recovery_flow:
            return
        flow_id = self._recovery_flow.get("flow_id")
        kind = self._recovery_flow.get("kind", "qr")
        try:
            with self.remote_guard():
                if kind == "sms":
                    self._recovery_client.cancel_webvpn_sms_login(flow_id)
                else:
                    self._recovery_client.cancel_webvpn_qr_login(flow_id)
        except Exception:
            self.logger.debug(
                "[成绩追踪] 取消旧恢复会话失败",
                exc_info=True,
            )

    @staticmethod
    def _recovery_flow_payload(kind: str, flow: dict[str, Any]) -> dict[str, Any]:
        payload = {"kind": kind, **flow}
        try:
            expires_in = max(1, int(payload.get("expires_in", 300)))
        except (TypeError, ValueError):
            expires_in = 300
        payload["expires_in"] = expires_in
        payload["expires_at"] = time.time() + expires_in
        return payload

    @staticmethod
    def _update_recovery_flow(
        flow: dict[str, Any], result: dict[str, Any],
    ) -> None:
        """Apply safe challenge fields and renew the local visible deadline."""
        flow.update(
            {
                key: value
                for key, value in result.items()
                if key in {"captcha_image", "expires_in"}
            }
        )
        if "expires_in" in result:
            try:
                expires_in = max(1, int(result.get("expires_in") or 300))
            except (TypeError, ValueError):
                expires_in = 300
            flow["expires_in"] = expires_in
            flow["expires_at"] = time.time() + expires_in

    def _complete_recovery_locked(self) -> dict[str, Any]:
        client = self._recovery_client
        if client is None:
            raise ValueError("登录恢复流程不存在，请重新开始")
        if self.auth_setter:
            self.auth_setter(client)
        username = client.username or None
        self._recovery_client = None
        self._recovery_flow = None
        self._invalidate_recovery_link(cancel_flow=False)
        with self._lock:
            self._state.update(
                stage="scheduled",
                message="登录已恢复，准备检查最新成绩",
                next_check_at=_iso(),
                last_error=None,
            )
            self._save_state()
        self._wake.set()
        return {"status": "authenticated", "username": username}

    def start_recovery_login(self, token: str) -> dict[str, Any]:
        with self._recovery_lock:
            with self._lock:
                if not self._recovery_token_is_valid(token):
                    raise ValueError("一次性登录链接不存在或已失效")
            if self._recovery_client and self._recovery_flow:
                self._cancel_recovery_flow_locked()
                self._recovery_client = None
                self._recovery_flow = None
            if not self.qr_login_starter:
                raise RuntimeError("当前运行环境不支持二维码恢复")
            with self.remote_guard():
                client, flow = self.qr_login_starter()
            self._recovery_client = client
            self._recovery_flow = self._recovery_flow_payload("qr", flow)
            with self._lock:
                self._state.update(
                    stage="waiting_qr",
                    message="一次性登录页面已打开，五分钟内等待微信扫码确认",
                )
                self._save_state()
            return {"status": "pending", **flow}

    def poll_recovery_login(self, token: str) -> dict[str, Any]:
        with self._recovery_lock:
            with self._lock:
                if not self._recovery_token_is_valid(token):
                    raise ValueError("一次性登录链接不存在或已失效")
            if not self._recovery_client or not self._recovery_flow:
                return {"status": "not_started"}
            if self._recovery_flow.get("kind") != "qr":
                return {
                    "status": "sms_required",
                    **{
                        key: value
                        for key, value in self._recovery_flow.items()
                        if key in {"flow_id", "captcha_image", "expires_in"}
                    },
                }
            try:
                with self.remote_guard():
                    result = self._recovery_client.poll_webvpn_qr_login(
                        self._recovery_flow["flow_id"]
                    )
            except Exception as error:
                self.logger.warning(
                    "[成绩追踪] 网页恢复二维码轮询失败：%s",
                    type(error).__name__,
                )
                return {"status": "pending", "message": "状态查询暂时失败，正在重试"}
            status = result.get("status")
            if status == "authenticated":
                return self._complete_recovery_locked()
            if status == "sms_required":
                self._recovery_flow = self._recovery_flow_payload("sms", result)
                with self._lock:
                    self._state.update(
                        stage="waiting_sms",
                        message="扫码已确认，等待图形验证码和短信验证",
                    )
                    self._save_state()
                return result
            if status in {"expired", "error"}:
                self._recovery_client = None
                self._recovery_flow = None
            return result

    def _require_sms_recovery_locked(self, token: str) -> tuple[Any, dict[str, Any]]:
        with self._lock:
            if not self._recovery_token_is_valid(token):
                raise ValueError("一次性登录链接不存在或已失效")
        if (
            not self._recovery_client
            or not self._recovery_flow
            or self._recovery_flow.get("kind") != "sms"
        ):
            raise ValueError("短信验证流程不存在，请重新开始登录")
        return self._recovery_client, self._recovery_flow

    def refresh_recovery_captcha(self, token: str) -> dict[str, Any]:
        with self._recovery_lock:
            client, flow = self._require_sms_recovery_locked(token)
            with self.remote_guard():
                result = client.refresh_webvpn_captcha(flow["flow_id"])
            self._update_recovery_flow(flow, result)
            return {"success": True, **result}

    def send_recovery_sms(self, token: str, captcha_code: str) -> dict[str, Any]:
        with self._recovery_lock:
            client, flow = self._require_sms_recovery_locked(token)
            with self.remote_guard():
                result = client.send_webvpn_sms_code(
                    flow["flow_id"], captcha_code
                )
            self._update_recovery_flow(flow, result)
            if result.get("status") == "sent":
                with self._lock:
                    self._state.update(
                        stage="waiting_sms",
                        message="短信验证码已发送，等待用户提交",
                    )
                    self._save_state()
            return {"success": result.get("status") == "sent", **result}

    def verify_recovery_sms(
        self,
        token: str,
        code: str,
        trust_device: bool = False,
    ) -> dict[str, Any]:
        with self._recovery_lock:
            client, flow = self._require_sms_recovery_locked(token)
            with self.remote_guard():
                result = client.verify_webvpn_sms_code(
                    flow["flow_id"], code, trust_device
                )
            if result.get("status") == "authenticated":
                return self._complete_recovery_locked()
            self._update_recovery_flow(flow, result)
            return {"success": False, **result}

    def cancel_recovery_login(self, token: str) -> dict[str, Any]:
        with self._recovery_lock:
            with self._lock:
                if not self._recovery_token_is_valid(token):
                    raise ValueError("一次性登录链接不存在或已失效")
            self._cancel_recovery_flow_locked()
            self._recovery_client = None
            self._recovery_flow = None
            with self._lock:
                self._state.update(
                    stage="waiting_login",
                    message="登录恢复已取消，可在一次性页面重新开始",
                )
                self._save_state()
            return {"success": True, "status": "ready"}

    def get_recovery_status(self, token: str) -> dict[str, Any]:
        with self._lock:
            if not self._recovery_token_is_valid(token):
                raise ValueError("一次性登录链接不存在或已失效")
        with self._recovery_lock:
            if not self._recovery_flow:
                return {"status": "ready"}
            kind = self._recovery_flow.get("kind", "qr")
            status = "sms_required" if kind == "sms" else "qr_pending"
            try:
                expires_in = max(
                    0,
                    int(float(self._recovery_flow.get("expires_at", 0)) - time.time()),
                )
            except (TypeError, ValueError):
                expires_in = int(self._recovery_flow.get("expires_in", 300))
            return {
                "status": status,
                "expires_in": expires_in,
                **{
                    key: value
                    for key, value in self._recovery_flow.items()
                    if key in {"flow_id", "qr_content", "poll_interval", "captcha_image"}
                },
            }

    def invalidate_recovery_link(self) -> None:
        self._invalidate_recovery_link()

    def _invalidate_recovery_link(self, cancel_flow: bool = True) -> None:
        with self._recovery_lock:
            if cancel_flow:
                self._cancel_recovery_flow_locked()
            self._recovery_client = None
            self._recovery_flow = None
            with self._lock:
                token_hash = self._state.pop("recovery_token_hash", None)
                issued_at = self._state.pop("recovery_token_issued_at", None)
                changed = bool(token_hash or issued_at)
                if changed:
                    self._save_state()

    @staticmethod
    def _course_key(item: dict[str, Any]) -> str:
        return score_key(item)

    def _build_snapshot(
        self,
        scores: list[Any],
        overall_gpa: Any,
        revision: str = "",
    ) -> dict[str, Any]:
        courses = []
        for score in scores:
            source = score if isinstance(score, dict) else vars(score)
            item = {
                key: source.get(key)
                for key in SCORE_FIELDS
            }
            item["key"] = self._course_key(item)
            courses.append(item)
        content = {
            "overall_gpa": overall_gpa,
            "courses": sorted(courses, key=lambda item: item["key"]),
        }
        import hashlib

        digest = hashlib.sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return {
            "updated_at": _iso(),
            "hash": revision or digest,
            "revision": revision or digest,
            **content,
        }

    @staticmethod
    def _compare(
        previous: dict[str, Any],
        current: dict[str, Any],
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        bool,
    ]:
        result = diff_scores(
            {
                "scores": previous.get("courses", []),
                "overall_gpa": previous.get("overall_gpa"),
            },
            {
                "scores": current.get("courses", []),
                "overall_gpa": current.get("overall_gpa"),
            },
        )
        return (
            list(result["added"]),
            list(result["changed"]),
            list(result["removed"]),
            bool(result["overall_gpa_changed"]),
        )

    def handle_scores_revision(
        self,
        account_id: str,
        revision: str,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> dict[str, Any]:
        with self._revision_lock:
            return self._handle_scores_revision_locked(
                account_id,
                revision,
                payload,
                reason=reason,
            )

    def _handle_scores_revision_locked(
        self,
        account_id: str,
        revision: str,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Consume one committed score revision from any producer."""
        if not revision:
            return {"change_count": 0}
        with self._lock:
            if not self._config.get("enabled"):
                return {"change_count": 0}
            if str(self._state.get("account_id") or "") != str(account_id):
                return {"change_count": 0}
            configured_activation_id = str(
                self._config.get("_activation_id") or ""
            )
            activation_key = f"activation:{configured_activation_id}"
            activation_id = (
                configured_activation_id
                if configured_activation_id
                and not any(
                    str(item.get("dedupe_key") or "") == activation_key
                    for item in self._outbox
                )
                else ""
            )
            if not activation_id and not self._within_window(_now()):
                self._state["pending_revision"] = revision
                self._save_state()
                return {"change_count": 0}
            if (
                not activation_id
                and self._state.get("last_seen_revision") == revision
            ):
                return {"change_count": 0}
            config = self._config.copy()
        snapshot = self._build_snapshot(
            list(payload.get("scores") or []),
            payload.get("overall_gpa"),
            revision,
        )
        previous = self._read_json(self.snapshot_path, {})
        additions, changes, removals, overall_gpa_changed = self._compare(
            previous, snapshot
        )
        # Notification-only detail annotations must never enter the persisted
        # score baseline used by future change detection.
        additions = [dict(item) for item in additions]
        changes = [
            {"before": dict(item["before"]), "after": dict(item["after"])}
            for item in changes
        ]
        removals = [dict(item) for item in removals]

        should_notify = bool(config.get("enabled"))
        notification = None
        if should_notify and activation_id:
            notification = (
                "[NEU 成绩追踪] 已开启并完成初始同步",
                self._initial_email(
                    snapshot,
                    opening="成绩追踪已开启，并完成本次初始成绩同步。",
                ),
                f"activation:{activation_id}",
            )
        elif should_notify and not previous:
            notification = (
                "[NEU 成绩追踪] 首次成绩同步完成",
                self._initial_email(snapshot),
                f"revision:{revision}",
            )
        elif should_notify and previous and (
            additions or changes or removals or overall_gpa_changed
        ):
            if self.score_detail_lookup:
                detail_targets = list(additions)
                detail_targets.extend(
                    item["after"]
                    for item in changes
                    if (
                        item["before"].get("score") != item["after"].get("score")
                        or item["before"].get("gpa") != item["after"].get("gpa")
                    )
                )
                for course in detail_targets:
                    try:
                        course["_score_detail"] = self.score_detail_lookup(
                            account_id,
                            course,
                        )
                    except Exception:
                        course["_score_detail"] = {"status": "failed"}
            notification = (
                "[NEU 成绩追踪] 检测到成绩更新",
                self._change_email(
                    previous,
                    snapshot,
                    additions,
                    changes,
                    removals,
                    overall_gpa_changed,
                ),
                f"revision:{revision}",
            )

        self._write_json(self.snapshot_path, snapshot)

        with self._lock:
            current_activation_id = str(self._config.get("_activation_id") or "")
            activation_is_current = bool(
                activation_id
                and self._config.get("enabled")
                and current_activation_id == activation_id
            )
            self._state["last_seen_revision"] = revision
            self._state.pop("pending_revision", None)
            if (
                notification
                and self._config.get("enabled")
                and (
                    activation_is_current
                    or self._state.get("last_notified_revision") != revision
                )
                and (not activation_id or activation_is_current)
            ):
                self._queue_email(*notification)
                self._state["last_notified_revision"] = revision
            self._state["last_revision_reason"] = reason
            self._state["last_change_count"] = (
                len(additions)
                + len(changes)
                + len(removals)
                + int(overall_gpa_changed)
                if previous else 0
            )
            self._save_state()
        if notification:
            # SMTP belongs to the tracking scheduler, never a cache worker.
            self._wake.set()
        return {
            "additions": additions,
            "changes": changes,
            "removals": removals,
            "overall_gpa_changed": overall_gpa_changed,
            "change_count": (
                len(additions)
                + len(changes)
                + len(removals)
                + int(overall_gpa_changed)
                if previous
                else 0
            ),
        }

    @staticmethod
    def _format_course(item: dict[str, Any]) -> str:
        return (
            f"{item.get('name', '')}（{item.get('code', '')}，"
            f"{item.get('credit', '')} 学分，成绩 {item.get('score', '') or '未出分'}，"
            f"绩点 {item.get('gpa', '')}，{item.get('term_display', item.get('term', ''))}）"
        )

    @staticmethod
    def _format_score_detail(item: dict[str, Any]) -> str:
        detail = item.get("_score_detail") or {}
        status = detail.get("status")
        if status == "available":
            parts = []
            for index, score_item in enumerate(detail.get("item_scores") or [], 1):
                if not isinstance(score_item, dict):
                    continue
                name = str(score_item.get("name") or score_item.get("code") or f"分项 {index}")
                value = score_item.get("value")
                parts.append(f"{name} {value if value not in (None, '') else '暂无'}")
            return "；".join(parts) if parts else "暂无可用分项成绩"
        if status == "no_data":
            return "暂无可用分项成绩"
        return "本次未能获取分项成绩"

    @classmethod
    def _format_course_with_detail(cls, item: dict[str, Any]) -> str:
        return f"{cls._format_course(item)}\n  分项成绩：{cls._format_score_detail(item)}"

    def _initial_email(
        self,
        snapshot: dict[str, Any],
        *,
        opening: str = "成绩追踪已完成首次同步。",
    ) -> str:
        rows = "\n".join(f"- {self._format_course(item)}" for item in snapshot["courses"]) or "无"
        return (
            f"{opening}\n\n"
            f"课程数：{len(snapshot['courses'])}\n"
            f"总 GPA：{snapshot.get('overall_gpa') if snapshot.get('overall_gpa') is not None else '未知'}\n\n"
            f"{rows}\n\n检查时间：{snapshot['updated_at']}"
        )

    def _change_email(
        self,
        previous: dict[str, Any],
        current: dict[str, Any],
        additions: list[dict[str, Any]],
        changes: list[dict[str, Any]],
        removals: list[dict[str, Any]],
        overall_gpa_changed: bool,
    ) -> str:
        new_rows = "\n".join(
            f"- {self._format_course_with_detail(item)}" for item in additions
        ) or "无"
        field_labels = {
            "name": "课程名称",
            "score": "成绩",
            "gpa": "绩点",
            "credit": "学分",
            "term": "学期",
            "term_display": "学期名称",
            "course_type": "课程类型",
            "course_category": "课程类别",
            "general_category": "通识类别",
            "exam_type": "考核方式",
            "exam_status": "考试状态",
            "course_nature": "课程性质",
            "is_passed": "是否通过",
        }
        changed_lines = []
        for item in changes:
            before = item["before"]
            after = item["after"]
            details = [
                f"{field_labels[field]}：{before.get(field)!s} → {after.get(field)!s}"
                for field in SCORE_FIELDS
                if field != "code" and before.get(field) != after.get(field)
            ]
            changed_lines.append(
                f"- {after.get('name') or before.get('name') or after.get('code')}："
                + "；".join(details)
                + (
                    f"\n  分项成绩：{self._format_score_detail(after)}"
                    if after.get("_score_detail") is not None
                    else ""
                )
            )
        changed_rows = "\n".join(changed_lines) or "无"
        removed_rows = "\n".join(
            f"- {self._format_course(item)}" for item in removals
        ) or "无"
        return (
            "检测到成绩变化。\n\n"
            f"原总 GPA：{previous.get('overall_gpa', '未知')}\n"
            f"新总 GPA：{current.get('overall_gpa', '未知')}\n\n"
            f"总 GPA 是否变化：{'是' if overall_gpa_changed else '否'}\n\n"
            f"新增课程：\n{new_rows}\n\n"
            f"成绩修正：\n{changed_rows}\n\n"
            f"移除课程：\n{removed_rows}\n\n"
            f"检查时间：{current['updated_at']}"
        )

    def _queue_email(
        self, subject: str, body: str, dedupe_key: str, *, html_body: str = "",
    ) -> None:
        with self._lock:
            if any(item.get("dedupe_key") == dedupe_key for item in self._outbox):
                return
            message = {
                "id": str(uuid.uuid4()),
                "dedupe_key": dedupe_key,
                "subject": subject,
                "body": body,
                "html_body": html_body,
                "created_at": _iso(),
                "attempts": 0,
            }
            if dedupe_key.startswith("activation:"):
                self._outbox.insert(0, message)
            else:
                self._outbox.append(message)
            self._save_outbox()

    def _flush_outbox(self) -> None:
        with self._lock:
            if not self._outbox:
                return
            config = self._config.copy()
            message = self._outbox[0].copy()
            dedupe_key = str(message.get("dedupe_key") or "")
            if dedupe_key.startswith("activation:") and (
                not config.get("enabled")
                or dedupe_key != f"activation:{config.get('_activation_id', '')}"
            ):
                self._outbox.pop(0)
                self._save_outbox()
                return
        try:
            self._validate_config(config, require_complete=True)
            if message.get("html_body"):
                self._send_email(
                    config, message["subject"], message["body"], message["html_body"],
                )
            else:
                self._send_email(config, message["subject"], message["body"])
        except Exception as error:
            with self._lock:
                if self._outbox and self._outbox[0]["id"] == message["id"]:
                    self._outbox[0]["attempts"] = int(message.get("attempts", 0)) + 1
                    self._outbox[0]["last_attempt_at"] = _iso()
                    self._outbox[0]["last_error"] = type(error).__name__
                    self._save_outbox()
            return
        with self._lock:
            if self._outbox and self._outbox[0]["id"] == message["id"]:
                if dedupe_key.startswith("activation:"):
                    activation_id = dedupe_key.removeprefix("activation:")
                    if str(self._config.get("_activation_id") or "") == activation_id:
                        candidate = self._config.copy()
                        candidate.pop("_activation_id", None)
                        candidate["_activation_delivered_id"] = activation_id
                        self._write_json(self.config_path, candidate)
                        self._config = candidate
                self._outbox.pop(0)
                self._state["last_notification_at"] = _iso()
                self._save_outbox()
                self._save_state()

    @staticmethod
    def _send_email(
        config: dict[str, Any], subject: str, body: str, html_body: str = "",
    ) -> None:
        if html_body:
            message: Any = MIMEMultipart("alternative")
            message.attach(MIMEText(body, "plain", "utf-8"))
            message.attach(MIMEText(html_body, "html", "utf-8"))
        else:
            message = MIMEText(body, "plain", "utf-8")
        message["Subject"] = Header(subject, "utf-8")
        message["From"] = config["from_email"]
        message["To"] = config["to_email"]
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid()
        security = config["smtp_security"]
        host = config["smtp_host"]
        port = int(config["smtp_port"])
        context = ssl.create_default_context()
        if security == "ssl":
            connection: Any = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
        else:
            connection = smtplib.SMTP(host, port, timeout=15)
        with connection:
            if security == "starttls":
                connection.starttls(context=context)
            if config.get("smtp_username"):
                connection.login(config["smtp_username"], config.get("smtp_password", ""))
            connection.sendmail(
                config["from_email"],
                [config["to_email"]],
                message.as_string(),
            )

    def _record_error(self, message: str) -> None:
        with self._lock:
            self._state.update(
                stage="error",
                message="成绩检查失败，稍后将自动重试",
                last_error=message,
                next_check_at=(
                    _now() + timedelta(minutes=int(self._config["interval_minutes"]))
                ).isoformat(),
            )
            self._save_state()

    def _save_state(self) -> None:
        self._write_json(self.state_path, self._state)

    def _save_outbox(self) -> None:
        self._write_json(self.outbox_path, {"messages": self._outbox})
