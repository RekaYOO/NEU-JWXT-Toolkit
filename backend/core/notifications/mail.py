"""Process-wide SMTP configuration and durable notification outbox."""

from __future__ import annotations

import json
import smtplib
import ssl
import threading
import uuid
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any, Callable

from backend.core.runtime.config import secure_file


CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_MAIL_CONFIG: dict[str, Any] = {
    "smtp_host": "",
    "smtp_port": 465,
    "smtp_security": "ssl",
    "smtp_username": "",
    "smtp_password": "",
    "from_email": "",
    "to_email": "",
}
_LEGACY_SMTP_FIELDS = frozenset(DEFAULT_MAIL_CONFIG)


def _iso() -> str:
    return datetime.now(CHINA_TZ).isoformat()


class SystemMailService:
    """Own the single SMTP channel and its persistent retry queue."""

    def __init__(self, data_dir: str | Path, logger: Any) -> None:
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.config_path = root / "system_mail_config.json"
        self.outbox_path = root / "system_mail_outbox.json"
        self.legacy_config_path = root / "grade_tracking_config.json"
        self.legacy_outbox_path = root / "grade_tracking_outbox.json"
        self.migration_path = root / ".system_mail_migrated_v1"
        self.logger = logger
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._materializers: dict[str, Callable[[dict[str, Any]], dict[str, str] | None]] = {}
        self._delivery_listeners: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._delivery_validators: dict[str, Callable[[dict[str, Any]], bool]] = {}
        self._migrate_legacy_files()
        self._config = {
            **DEFAULT_MAIL_CONFIG,
            **self._read_json(self.config_path, {}),
        }
        messages = self._read_json(self.outbox_path, {"messages": []}).get("messages", [])
        self._outbox = list(messages) if isinstance(messages, list) else []

    @staticmethod
    def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else fallback.copy()
        except (OSError, ValueError, TypeError):
            return fallback.copy()

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        secure_file(path)

    @staticmethod
    def _is_legacy_recovery_message(item: dict[str, Any]) -> bool:
        dedupe = str(item.get("dedupe_key") or "")
        content = "\n".join(str(item.get(key) or "") for key in ("body", "html_body"))
        return (
            dedupe.startswith(("recovery-link:", "login-required:"))
            or "/grade-tracking/recovery/" in content
        )

    @staticmethod
    def _legacy_source(item: dict[str, Any]) -> str:
        dedupe = str(item.get("dedupe_key") or "")
        return "course_selection" if dedupe.startswith("course-selection:") else "grade_tracking"

    def _migrate_legacy_files(self) -> None:
        if self.migration_path.exists():
            return
        config = self._read_json(self.config_path, {})
        legacy_config = self._read_json(self.legacy_config_path, {})
        if not config:
            config = {
                key: legacy_config.get(key, default)
                for key, default in DEFAULT_MAIL_CONFIG.items()
            }
            self._write_json(self.config_path, config)

        current = self._read_json(self.outbox_path, {"messages": []}).get("messages", [])
        migrated = list(current) if isinstance(current, list) else []
        known_ids = {str(item.get("id") or "") for item in migrated if isinstance(item, dict)}
        legacy_messages = self._read_json(
            self.legacy_outbox_path, {"messages": []},
        ).get("messages", [])
        if isinstance(legacy_messages, list):
            for raw in legacy_messages:
                if not isinstance(raw, dict) or self._is_legacy_recovery_message(raw):
                    continue
                item = dict(raw)
                if str(item.get("id") or "") in known_ids:
                    continue
                item["source"] = self._legacy_source(item)
                item.setdefault("kind", "message")
                migrated.append(item)
        self._write_json(self.outbox_path, {"messages": migrated})
        if self.legacy_outbox_path.exists():
            self._write_json(self.legacy_outbox_path, {"messages": []})
        self.migration_path.write_text("1\n", encoding="ascii")
        secure_file(self.migration_path)

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._sender_loop, name="system-mail", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)

    def register_materializer(
        self, kind: str, provider: Callable[[dict[str, Any]], dict[str, str] | None],
    ) -> None:
        self._materializers[str(kind)] = provider

    def register_delivery_listener(
        self, source: str, listener: Callable[[dict[str, Any]], None],
    ) -> None:
        self._delivery_listeners.setdefault(str(source), []).append(listener)

    def register_delivery_validator(
        self, source: str, validator: Callable[[dict[str, Any]], bool],
    ) -> None:
        self._delivery_validators[str(source)] = validator

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            result = {key: value for key, value in self._config.items() if key != "smtp_password"}
            result["smtp_password_configured"] = bool(self._config.get("smtp_password"))
            return result

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            configured = self.is_configured()
            return {
                "configured": configured,
                "status": "邮件通道可用" if configured else "请前往系统设置配置邮件",
                "pending_notifications": len(self._outbox),
            }

    def is_configured(self) -> bool:
        with self._lock:
            return bool(
                self._config.get("smtp_host")
                and self._config.get("from_email")
                and self._config.get("to_email")
                and (self._config.get("smtp_password") or self._config.get("smtp_username"))
            )

    @staticmethod
    def _validate(config: dict[str, Any], *, require_complete: bool) -> None:
        if config.get("smtp_security") not in {"ssl", "starttls", "none"}:
            raise ValueError("不支持的 SMTP 安全方式")
        if not 1 <= int(config.get("smtp_port", 0)) <= 65535:
            raise ValueError("SMTP 端口无效")
        if require_complete:
            missing = [
                label for key, label in (
                    ("smtp_host", "SMTP 服务器"),
                    ("from_email", "发件地址"),
                    ("to_email", "收件地址"),
                ) if not str(config.get(key, "")).strip()
            ]
            if config.get("smtp_username") and not config.get("smtp_password"):
                missing.append("SMTP 密码")
            if missing:
                raise ValueError("请填写：" + "、".join(missing))

    def update_config(self, values: dict[str, Any]) -> dict[str, Any]:
        incoming = {key: value for key, value in dict(values).items() if key in _LEGACY_SMTP_FIELDS or key == "clear_smtp_password"}
        with self._lock:
            candidate = self._config.copy()
            password = incoming.pop("smtp_password", None)
            clear_password = bool(incoming.pop("clear_smtp_password", False))
            candidate.update(incoming)
            if password:
                candidate["smtp_password"] = password
            elif clear_password:
                candidate["smtp_password"] = ""
            self._validate(candidate, require_complete=False)
            self._write_json(self.config_path, candidate)
            self._config = candidate
        self._wake.set()
        return self.get_config()

    def test_email(self) -> None:
        with self._lock:
            config = self._config.copy()
        self._validate(config, require_complete=True)
        self._send_email(
            config,
            "[NEU 教务工具箱] 系统邮件配置测试",
            "这是一封系统邮件配置测试邮件。\n\n如果你能收到这封邮件，说明当前 SMTP 服务器、端口、安全方式和账号配置可以正常发送邮件。\n\n此测试不代表任何具体业务功能，仅用于验证系统邮件通道。",
        )

    def queue_notification(
        self,
        source: str,
        subject: str,
        body: str,
        dedupe_key: str,
        html_body: str = "",
        *,
        kind: str = "message",
        template_metadata: dict[str, Any] | None = None,
        priority: bool = False,
    ) -> bool:
        if not self.is_configured():
            return False
        with self._lock:
            if any(item.get("dedupe_key") == dedupe_key for item in self._outbox):
                return False
            message = {
                "id": str(uuid.uuid4()),
                "source": str(source),
                "kind": str(kind),
                "dedupe_key": str(dedupe_key),
                "subject": str(subject),
                "body": str(body),
                "html_body": str(html_body),
                "template_metadata": dict(template_metadata or {}),
                "created_at": _iso(),
                "attempts": 0,
            }
            if priority:
                self._outbox.insert(0, message)
            else:
                self._outbox.append(message)
            self._save_outbox()
        self._wake.set()
        return True

    def pending_count(self, source: str | None = None) -> int:
        with self._lock:
            if source is None:
                return len(self._outbox)
            return sum(1 for item in self._outbox if item.get("source") == source)

    def has_pending(self, source: str, dedupe_key: str) -> bool:
        with self._lock:
            return any(
                item.get("source") == source and item.get("dedupe_key") == dedupe_key
                for item in self._outbox
            )

    def discard(
        self,
        *,
        source: str | None = None,
        dedupe_prefix: str = "",
        kind: str = "",
        template_context_ids: set[str] | None = None,
    ) -> None:
        context_ids = {str(value) for value in (template_context_ids or set()) if value}
        with self._lock:
            self._outbox = [
                item for item in self._outbox
                if not (
                    (source is None or item.get("source") == source)
                    and (not dedupe_prefix or str(item.get("dedupe_key") or "").startswith(dedupe_prefix))
                    and (not kind or str(item.get("kind") or "message") == kind)
                    and (
                        not context_ids
                        or str((item.get("template_metadata") or {}).get("context_id") or "")
                        in context_ids
                    )
                )
            ]
            self._save_outbox()

    def _sender_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.flush_once()
            except Exception:
                self.logger.exception("[系统邮件] 发送线程异常")
            self._wake.wait(20)
            self._wake.clear()

    def _materialize(self, message: dict[str, Any]) -> dict[str, Any] | None:
        kind = str(message.get("kind") or "message")
        if kind == "message":
            return message
        provider = self._materializers.get(kind)
        if provider is None:
            return None
        rendered = provider(dict(message))
        return {**message, **rendered} if rendered else None

    def flush_once(self) -> None:
        with self._lock:
            if not self._outbox:
                return
            config = self._config.copy()
            stored = self._outbox[0].copy()
        rendered = self._materialize(stored)
        validator = self._delivery_validators.get(str(stored.get("source") or ""))
        if validator is not None and not validator(stored):
            self._drop_head(stored["id"])
            return
        if rendered is None:
            self._drop_head(stored["id"])
            return
        try:
            self._validate(config, require_complete=True)
            if rendered.get("html_body"):
                self._send_email(
                    config, rendered["subject"], rendered["body"], rendered["html_body"],
                )
            else:
                self._send_email(config, rendered["subject"], rendered["body"])
        except Exception as error:
            with self._lock:
                if self._outbox and self._outbox[0].get("id") == stored.get("id"):
                    self._outbox[0]["attempts"] = int(stored.get("attempts", 0)) + 1
                    self._outbox[0]["last_attempt_at"] = _iso()
                    self._outbox[0]["last_error"] = type(error).__name__
                    self._save_outbox()
            return
        for listener in self._delivery_listeners.get(str(stored.get("source") or ""), []):
            try:
                listener(stored)
            except Exception:
                self.logger.exception("[系统邮件] 发送完成回调失败")
        self._drop_head(stored["id"])

    def _drop_head(self, message_id: str) -> None:
        with self._lock:
            if self._outbox and self._outbox[0].get("id") == message_id:
                self._outbox.pop(0)
                self._save_outbox()

    @staticmethod
    def _send_email(config: dict[str, Any], subject: str, body: str, html_body: str = "") -> None:
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
        context = ssl.create_default_context()
        if config["smtp_security"] == "ssl":
            connection: Any = smtplib.SMTP_SSL(config["smtp_host"], int(config["smtp_port"]), timeout=15, context=context)
        else:
            connection = smtplib.SMTP(config["smtp_host"], int(config["smtp_port"]), timeout=15)
        with connection:
            if config["smtp_security"] == "starttls":
                connection.starttls(context=context)
            if config.get("smtp_username"):
                connection.login(config["smtp_username"], config.get("smtp_password", ""))
            connection.sendmail(config["from_email"], [config["to_email"]], message.as_string())

    def _save_outbox(self) -> None:
        self._write_json(self.outbox_path, {"messages": self._outbox})
