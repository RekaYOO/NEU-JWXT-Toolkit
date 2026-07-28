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
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any, Callable

from backend.core.runtime.config import secure_file


CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "interval_minutes": 30,
    "start_hour": 9,
    "end_hour": 21,
    "notify_initial": True,
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
        self._lock = threading.RLock()
        self._check_lock = threading.Lock()
        self._recovery_lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._recovery_client: Any | None = None
        self._recovery_flow: dict[str, Any] | None = None
        self._config = {**DEFAULT_CONFIG, **self._read_json(self.config_path, {})}
        self._config["interval_minutes"] = max(
            5,
            int(self._config.get("interval_minutes", 30)),
        )
        self._state = {**DEFAULT_STATE, **self._read_json(self.state_path, {})}
        self._outbox = list(self._read_json(self.outbox_path, {"messages": []}).get("messages", []))

    @staticmethod
    def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
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
            if self._recovery_client and self._recovery_flow:
                self._recovery_client.cancel_webvpn_qr_login(
                    self._recovery_flow.get("flow_id")
                )
            self._recovery_client = None
            self._recovery_flow = None
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            result = {key: value for key, value in self._config.items() if key != "smtp_password"}
            result["smtp_password_configured"] = bool(self._config.get("smtp_password"))
            return result

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._state,
                "enabled": bool(self._config.get("enabled")),
                "pending_notifications": len(self._outbox),
            }

    def update_config(self, values: dict[str, Any]) -> dict[str, Any]:
        site_url_changed = False
        with self._lock:
            previous_site_url = str(self._config.get("site_url", "")).strip()
            password = values.pop("smtp_password", None)
            clear_password = values.pop("clear_smtp_password", False)
            self._config.update(values)
            if password:
                self._config["smtp_password"] = password
            elif clear_password:
                self._config["smtp_password"] = ""
            self._validate_config(self._config, require_complete=bool(self._config["enabled"]))
            site_url_changed = (
                str(self._config.get("site_url", "")).strip() != previous_site_url
            )
            self._write_json(self.config_path, self._config)
            if self._config["enabled"]:
                self._state.update(
                    stage="scheduled",
                    message="成绩追踪已启用，等待下一次检查",
                    next_check_at=_iso(),
                    last_error=None,
                )
            else:
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
            "[NEU 教务工具箱] 成绩追踪邮件测试",
            "邮件配置有效。\n\n成绩发生变化后，NEU 教务工具箱会通过此地址发送通知。",
        )

    def check_now(self) -> dict[str, Any]:
        return self._run_check(manual=True)

    def _scheduler(self) -> None:
        while not self._stop.is_set():
            try:
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
                            message="正在等待当前二维码或短信认证完成",
                            next_check_at=(
                                _now() + timedelta(minutes=int(config["interval_minutes"]))
                            ).isoformat(),
                        )
                        self._save_state()
                    return self.get_status()
                auth = self._handle_login_required(config)
                if auth is None:
                    return self.get_status()
            else:
                self._invalidate_recovery_link()

            scores = auth.academic.get_scores()
            overall_gpa = auth.academic.get_overall_gpa()
            snapshot = self._build_snapshot(scores, overall_gpa)
            previous = self._read_json(self.snapshot_path, {})
            additions, changes = self._compare(previous, snapshot)

            self._write_json(self.snapshot_path, snapshot)
            metadata = {
                "fetch_time": _iso(),
                "username": auth.username,
                "total_courses": len(scores),
                "overall_gpa": overall_gpa,
                "source": "grade_tracking",
            }
            self.score_storage.save_scores(scores, metadata=metadata)
            if self.report_storage and (not previous or additions or changes):
                try:
                    report_result = self.report_storage.refresh_report(auth)
                except Exception as exc:
                    self.logger.warning(
                        "[成绩追踪] 成绩已同步，但培养计划刷新失败：%s",
                        exc,
                    )
                else:
                    if not report_result.get("success"):
                        self.logger.warning(
                            "[成绩追踪] 成绩已同步，但培养计划刷新失败：%s",
                            report_result.get("message", "未知错误"),
                        )

            notification = None
            if not previous and config.get("notify_initial"):
                notification = (
                    "[NEU 成绩追踪] 首次成绩同步完成",
                    self._initial_email(snapshot),
                    f"initial:{snapshot['hash']}",
                )
            elif additions or changes:
                notification = (
                    "[NEU 成绩追踪] 检测到成绩更新",
                    self._change_email(previous, snapshot, additions, changes),
                    f"update:{snapshot['hash']}",
                )
            if notification:
                self._queue_email(*notification)
                self._flush_outbox()

            with self._lock:
                self._state.update(
                    stage="monitoring",
                    message="成绩检查完成，追踪正在运行",
                    last_success_at=_iso(),
                    next_check_at=(
                        _now() + timedelta(minutes=int(config["interval_minutes"]))
                    ).isoformat(),
                    last_error=None,
                    course_count=len(scores),
                    overall_gpa=overall_gpa,
                    last_change_count=0 if not previous else len(additions) + len(changes),
                )
                self._save_state()
            return {
                **self.get_status(),
                "additions": additions,
                "changes": changes,
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
        if link:
            self._issue_recovery_link(link)
            return None
        return self._email_qr_login(config)

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
        with self._lock:
            if self._state.get("recovery_token_hash"):
                self._state.update(
                    stage="waiting_login",
                    message="等待用户打开邮件中的一次性登录链接",
                )
                self._save_state()
                return
            token = secrets.token_urlsafe(32)
            self._state.update(
                recovery_token_hash=self._token_hash(token),
                recovery_token_issued_at=_iso(),
                stage="waiting_login",
                message="等待用户打开邮件中的一次性登录链接",
                last_login_notice_at=time.time(),
            )
            self._save_state()
        recovery_url = (
            f"{site_url.rstrip('/')}/grade-tracking/recovery/{token}"
        )
        self._queue_email(
            "[NEU 成绩追踪] 请打开一次性链接恢复登录",
            "成绩追踪无法访问教务系统。\n\n"
            "请打开下面的一次性登录页面；访问页面后才会生成微信扫码二维码并开始五分钟轮询：\n"
            f"{recovery_url}\n\n"
            "网页链接会在成功建立教务会话后失效。单次二维码五分钟后失效，"
            "届时可在同一网页链接中重新生成。请勿转发此链接。",
            f"recovery-link:{self._token_hash(token)}",
        )
        self._flush_outbox()

    def start_recovery_login(self, token: str) -> dict[str, Any]:
        with self._recovery_lock:
            with self._lock:
                if not self._recovery_token_is_valid(token):
                    raise ValueError("一次性登录链接不存在或已失效")
            if self._recovery_client and self._recovery_flow:
                try:
                    self._recovery_client.cancel_webvpn_qr_login(
                        self._recovery_flow.get("flow_id")
                    )
                except Exception:
                    self.logger.debug(
                        "[成绩追踪] 重新生成二维码时取消旧会话失败",
                        exc_info=True,
                    )
                self._recovery_client = None
                self._recovery_flow = None
            if not self.qr_login_starter:
                raise RuntimeError("当前运行环境不支持二维码恢复")
            client, flow = self.qr_login_starter()
            self._recovery_client = client
            self._recovery_flow = flow
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
            try:
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
                client = self._recovery_client
                if self.auth_setter:
                    self.auth_setter(client)
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
                return {"status": "authenticated", "username": client.username or None}
            if status in {"expired", "error"}:
                self._recovery_client = None
                self._recovery_flow = None
            return result

    def get_recovery_status(self, token: str) -> dict[str, Any]:
        with self._lock:
            if not self._recovery_token_is_valid(token):
                raise ValueError("一次性登录链接不存在或已失效")
        with self._recovery_lock:
            if not self._recovery_flow:
                return {"status": "ready"}
            return {
                "status": "pending",
                "expires_in": int(self._recovery_flow.get("expires_in", 300)),
            }

    def invalidate_recovery_link(self) -> None:
        self._invalidate_recovery_link()

    def _invalidate_recovery_link(self, cancel_flow: bool = True) -> None:
        with self._recovery_lock:
            if cancel_flow and self._recovery_client and self._recovery_flow:
                self._recovery_client.cancel_webvpn_qr_login(
                    self._recovery_flow.get("flow_id")
                )
            self._recovery_client = None
            self._recovery_flow = None
            with self._lock:
                token_hash = self._state.pop("recovery_token_hash", None)
                issued_at = self._state.pop("recovery_token_issued_at", None)
                changed = bool(token_hash or issued_at)
                if changed:
                    self._save_state()

    def _email_qr_login(self, config: dict[str, Any]) -> Any | None:
        if not self.qr_login_starter:
            return None
        try:
            self._validate_config(config, require_complete=True)
            client, flow = self.qr_login_starter()
            qr_link = str(flow["qr_content"])
            expires_in = min(int(flow.get("expires_in", 300)), 300)
            self._send_email(
                config,
                "[NEU 成绩追踪] 请在五分钟内确认登录",
                "成绩追踪无法访问教务系统。\n\n"
                "请在五分钟内使用微信扫码并确认登录：\n"
                f"{qr_link}\n\n"
                "该链接对应本次一次性二维码，五分钟后自动失效。"
                "如果未能及时完成，请等待下一个成绩检查间隔，届时会收到新的登录邮件。",
            )
            with self._lock:
                self._state.update(
                    stage="waiting_qr",
                    message="登录邮件已发送，五分钟内等待微信扫码确认",
                    last_notification_at=_iso(),
                )
                self._save_state()
        except Exception as error:
            self.logger.exception("[成绩追踪] 登录二维码邮件发送失败")
            self._record_error(f"{type(error).__name__}: {error}")
            return None

        deadline = time.time() + expires_in
        while time.time() < deadline and not self._stop.is_set():
            try:
                result = client.poll_webvpn_qr_login(flow["flow_id"])
            except Exception as error:
                self.logger.warning("[成绩追踪] 二维码状态轮询失败：%s", type(error).__name__)
                if self._stop.wait(3):
                    break
                continue
            status = result.get("status")
            if status == "authenticated":
                if self.auth_setter:
                    self.auth_setter(client)
                with self._lock:
                    self._state.update(
                        stage="checking",
                        message="微信扫码登录已确认，继续本轮成绩检查",
                        last_error=None,
                    )
                    self._save_state()
                return client
            if status in {"expired", "error"}:
                break
            if self._stop.wait(3):
                break

        client.cancel_webvpn_qr_login(flow.get("flow_id"))
        with self._lock:
            self._state.update(
                stage="waiting_login",
                message="本次登录二维码已失效，将在下一个检查间隔重新发送",
            )
            self._save_state()
        return None

    @staticmethod
    def _course_key(item: dict[str, Any]) -> str:
        return "|".join(
            (
                str(item.get("term", "")),
                str(item.get("code", "")),
                str(item.get("exam_status", "") or "初修"),
            )
        )

    def _build_snapshot(self, scores: list[Any], overall_gpa: Any) -> dict[str, Any]:
        courses = []
        for score in scores:
            item = {
                "code": score.code,
                "name": score.name,
                "score": score.score,
                "gpa": score.gpa,
                "credit": score.credit,
                "term": score.term,
                "term_display": score.term_display,
                "exam_status": score.exam_status,
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
        return {"updated_at": _iso(), "hash": digest, **content}

    @staticmethod
    def _compare(
        previous: dict[str, Any],
        current: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        old = {item["key"]: item for item in previous.get("courses", [])}
        new = {item["key"]: item for item in current.get("courses", [])}
        additions = [new[key] for key in sorted(new.keys() - old.keys())]
        changes = []
        for key in sorted(new.keys() & old.keys()):
            if any(
                str(old[key].get(field, "")) != str(new[key].get(field, ""))
                for field in ("score", "gpa", "credit", "name")
            ):
                changes.append({"before": old[key], "after": new[key]})
        return additions, changes

    @staticmethod
    def _format_course(item: dict[str, Any]) -> str:
        return (
            f"{item.get('name', '')}（{item.get('code', '')}，"
            f"{item.get('credit', '')} 学分，成绩 {item.get('score', '') or '未出分'}，"
            f"绩点 {item.get('gpa', '')}，{item.get('term_display', item.get('term', ''))}）"
        )

    def _initial_email(self, snapshot: dict[str, Any]) -> str:
        rows = "\n".join(f"- {self._format_course(item)}" for item in snapshot["courses"]) or "无"
        return (
            "成绩追踪已完成首次同步。\n\n"
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
    ) -> str:
        new_rows = "\n".join(f"- {self._format_course(item)}" for item in additions) or "无"
        changed_rows = "\n".join(
            f"- {item['after']['name']}：{item['before'].get('score') or '未出分'} → "
            f"{item['after'].get('score') or '未出分'}"
            for item in changes
        ) or "无"
        return (
            "检测到成绩变化。\n\n"
            f"原总 GPA：{previous.get('overall_gpa', '未知')}\n"
            f"新总 GPA：{current.get('overall_gpa', '未知')}\n\n"
            f"新增课程：\n{new_rows}\n\n"
            f"成绩修正：\n{changed_rows}\n\n"
            f"检查时间：{current['updated_at']}"
        )

    def _queue_email(self, subject: str, body: str, dedupe_key: str) -> None:
        with self._lock:
            if any(item.get("dedupe_key") == dedupe_key for item in self._outbox):
                return
            self._outbox.append(
                {
                    "id": str(uuid.uuid4()),
                    "dedupe_key": dedupe_key,
                    "subject": subject,
                    "body": body,
                    "created_at": _iso(),
                    "attempts": 0,
                }
            )
            self._save_outbox()

    def _flush_outbox(self) -> None:
        with self._lock:
            if not self._outbox:
                return
            config = self._config.copy()
            message = self._outbox[0].copy()
        try:
            self._validate_config(config, require_complete=True)
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
                self._outbox.pop(0)
                self._state["last_notification_at"] = _iso()
                self._save_outbox()
                self._save_state()

    @staticmethod
    def _send_email(config: dict[str, Any], subject: str, body: str) -> None:
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
