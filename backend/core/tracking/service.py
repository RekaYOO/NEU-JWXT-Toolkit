"""Lightweight grade-change tracking built on the shared auth and score APIs."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
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
        mail_service: Any,
        auth_recovery_service: Any,
        report_storage: Any | None = None,
        login_flow_pending: Callable[[], bool] | None = None,
        score_refresher: Callable[[str, bool], dict[str, Any]] | None = None,
        score_detail_lookup: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        gpa_summary_provider: Callable[[str, list[dict]], dict] | None = None,
    ) -> None:
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.config_path = root / "grade_tracking_config.json"
        self.snapshot_path = root / "grade_tracking_snapshot.json"
        self.state_path = root / "grade_tracking_state.json"
        self.auth_provider = auth_provider
        self.score_storage = score_storage
        self.report_storage = report_storage
        self.logger = logger
        self.mail_service = mail_service
        self.auth_recovery_service = auth_recovery_service
        self.login_flow_pending = login_flow_pending
        self.score_refresher = score_refresher
        self.score_detail_lookup = score_detail_lookup
        self.gpa_summary_provider = gpa_summary_provider
        self._lock = threading.RLock()
        self._check_lock = threading.Lock()
        self._revision_lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._config = {**DEFAULT_CONFIG, **self._read_json(self.config_path, {})}
        legacy_config_present = False
        for legacy_key in (
            "site_url", "smtp_host", "smtp_port", "smtp_security",
            "smtp_username", "smtp_password", "from_email", "to_email",
        ):
            legacy_config_present = self._config.pop(legacy_key, None) is not None or legacy_config_present
        self._config.pop("notify_initial", None)
        try:
            interval = int(self._config.get("interval_minutes", 30))
        except (TypeError, ValueError):
            interval = int(DEFAULT_CONFIG["interval_minutes"])
        self._config["interval_minutes"] = min(1440, max(5, interval))
        self._state = {**DEFAULT_STATE, **self._read_json(self.state_path, {})}
        if self._state.get("message") == "tracking check completed":
            self._state["message"] = "成绩检查已完成"
        recovery_state_keys = {
            "recovery_token_hash", "recovery_token_issued_at",
            "recovery_target_service", "recovery_owner",
            "manual_login_notice_sent", "manual_login_notice_at",
            "last_login_notice_at",
        }
        state_cleaned = any(self._state.pop(key, None) is not None for key in recovery_state_keys)
        if legacy_config_present:
            self._write_json(self.config_path, self._config)
        if state_cleaned:
            self._write_json(self.state_path, self._state)
        # Keep the pending activation intent beside ``enabled`` so one atomic
        # config write records the complete switch transition.  Migrate the
        # earlier state marker (or a queued activation message) in memory.
        legacy_activation_id = str(
            self._state.pop("initial_notification_id", None) or ""
        )
        queued_activation_id = ""
        configured_activation_id = str(self._config.get("_activation_id") or "")
        if configured_activation_id and self.mail_service.has_pending(
            "grade_tracking", f"activation:{configured_activation_id}",
        ):
            queued_activation_id = configured_activation_id
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
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            return {key: value for key, value in self._config.items() if not key.startswith("_")}

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._state,
                "enabled": bool(self._config.get("enabled")),
                "pending_notifications": self.mail_service.pending_count("grade_tracking"),
            }

    def update_config(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {"enabled", "interval_minutes", "start_hour", "end_hour"}
        incoming = {key: value for key, value in dict(values).items() if key in allowed}
        # Removed setting: every disabled -> enabled transition now schedules
        # one initial notification unconditionally.
        incoming.pop("notify_initial", None)
        incoming.pop("_activation_id", None)
        with self._lock:
            previously_enabled = bool(self._config.get("enabled"))
            candidate = self._config.copy()
            candidate.update(incoming)
            if candidate.get("enabled") and not previously_enabled:
                candidate["_activation_id"] = str(uuid.uuid4())
            elif not candidate.get("enabled"):
                candidate.pop("_activation_id", None)
            self._validate_config(candidate, require_complete=bool(candidate["enabled"]))
            self._write_json(self.config_path, candidate)
            self._config = candidate
            if self._config["enabled"]:
                if not previously_enabled:
                    self.mail_service.discard(
                        source="grade_tracking", dedupe_prefix="activation:",
                    )
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
                self.mail_service.discard(
                    source="grade_tracking", dedupe_prefix="activation:",
                )
                self._state.update(
                    stage="disabled",
                    message="成绩追踪未启用",
                    next_check_at=None,
                )
            self._save_state()
        self._wake.set()
        return self.get_config()

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        """Persist and apply the tracking switch without changing form fields."""
        return self.update_config({"enabled": bool(enabled)})

    def _validate_config(self, config: dict[str, Any], require_complete: bool) -> None:
        interval = int(config.get("interval_minutes", 0))
        start = int(config.get("start_hour", -1))
        end = int(config.get("end_hour", -1))
        if not 5 <= interval <= 1440:
            raise ValueError("检查间隔必须在 5–1440 分钟之间")
        if not 0 <= start <= 23 or not 1 <= end <= 24 or start >= end:
            raise ValueError("每日检查时段必须是有效且递增的整点范围")
        if require_complete and not self.mail_service.is_configured():
            raise ValueError("启用前请先在系统设置中配置邮件")

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
                for path in (self.snapshot_path, self.state_path):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                self.mail_service.discard(source="grade_tracking")
                self._state = {
                    **DEFAULT_STATE,
                    "stage": "paused_logout",
                    "message": "教务登录已退出，重新登录后将自动恢复成绩追踪",
                }
            self._save_state()
        self._wake.set()

    def resume_after_login(self, account_id: str) -> None:
        with self._lock:
            previous_account = str(self._state.get("account_id") or "")
            has_unscoped_personal_state = bool(
                not previous_account
                and (
                    self.snapshot_path.exists()
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
                self.mail_service.discard(source="grade_tracking")
                self._state = {
                    **DEFAULT_STATE,
                    "account_id": str(account_id),
                }
            else:
                self._state["account_id"] = str(account_id)
            self.mail_service.discard(
                source="grade_tracking", dedupe_prefix="login-required:",
            )
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
            activation_already_queued = self.mail_service.has_pending(
                "grade_tracking", activation_key,
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
                self.auth_recovery_service.invalidate_matching(
                    source="grade_tracking",
                    target_service="primary",
                    account_id=str(
                        getattr(auth, "username", "")
                        or self._state.get("account_id")
                        or ""
                    ),
                )
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
                    message="成绩检查已完成",
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
        account_id = str(self._state.get("account_id") or "")
        self.auth_recovery_service.request_notification(
            source="grade_tracking",
            target_service="primary",
            account_id=account_id,
            subject="[NEU 成绩追踪] 登录已失效",
            body="成绩追踪无法访问教务系统，任务已暂停；恢复登录后会继续检查。",
            dedupe_key=f"login-required:{account_id}",
        )
        return None

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
                and not self.mail_service.has_pending(
                    "grade_tracking", activation_key,
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
                self.mail_service.queue_notification(
                    "grade_tracking", *notification,
                    priority=bool(activation_id),
                )
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

    def _calculated_gpa_text(self, snapshot: dict, label: str = "本地平均绩点") -> str:
        if not self.gpa_summary_provider:
            return ""
        summary = self.gpa_summary_provider(
            str(self._state.get("account_id") or ""), snapshot.get("courses") or [],
        )
        policy = summary["policy"]
        mode = "2025级及以后" if policy["mode"] == "from_2025" else "2024级及以前"
        average = summary["average"]
        value = f"{average:.4f}" if average is not None else "无计入课程"
        incomplete = policy["mode"] == "from_2025" and (
            not policy.get("report_available") or policy.get("missing_grading_scales")
        )
        note = "；分类/分制缓存待补全，结果暂供参考" if incomplete else ""
        return f"{label}（{mode}）：{value}{note}\n"

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
            f"{self._calculated_gpa_text(snapshot)}"
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
            f"{self._calculated_gpa_text(previous, '原本地平均绩点')}"
            f"{self._calculated_gpa_text(current, '新本地平均绩点')}\n"
            f"总 GPA 是否变化：{'是' if overall_gpa_changed else '否'}\n\n"
            f"新增课程：\n{new_rows}\n\n"
            f"成绩修正：\n{changed_rows}\n\n"
            f"移除课程：\n{removed_rows}\n\n"
            f"检查时间：{current['updated_at']}"
        )

    def handle_mail_delivered(self, message: dict[str, Any]) -> None:
        """Commit tracking delivery state after the shared mail service succeeds."""
        dedupe_key = str(message.get("dedupe_key") or "")
        with self._lock:
            if dedupe_key.startswith("activation:"):
                activation_id = dedupe_key.removeprefix("activation:")
                if str(self._config.get("_activation_id") or "") == activation_id:
                    candidate = self._config.copy()
                    candidate.pop("_activation_id", None)
                    candidate["_activation_delivered_id"] = activation_id
                    self._write_json(self.config_path, candidate)
                    self._config = candidate
            self._state["last_notification_at"] = _iso()
            self._save_state()

    def should_deliver_mail(self, message: dict[str, Any]) -> bool:
        dedupe_key = str(message.get("dedupe_key") or "")
        if not dedupe_key.startswith("activation:"):
            return True
        activation_id = dedupe_key.removeprefix("activation:")
        with self._lock:
            return bool(
                self._config.get("enabled")
                and str(self._config.get("_activation_id") or "") == activation_id
                and str(self._config.get("_activation_delivered_id") or "") != activation_id
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
