"""Token-scoped remote authentication recovery shared by background features."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets
import threading
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from backend.core.runtime.config import secure_file


CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_RECOVERY_CONFIG = {"public_base_url": ""}
MAX_STORED_CONTEXTS = 100
TERMINAL_CONTEXT_RETENTION = timedelta(days=30)


def _iso() -> str:
    return datetime.now(CHINA_TZ).isoformat()


class RemoteAuthRecoveryService:
    """Own persistent recovery contexts and process-local QR/SMS sessions."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        mail_service: Any,
        logger: Any,
        qr_login_starter: Callable[..., tuple[Any, dict[str, Any]]] | None = None,
        pending_sms_provider: Callable[..., tuple[Any, dict[str, Any]] | None] | None = None,
        auth_committer: Callable[..., None] | None = None,
        remote_guard: Callable[[], Any] | None = None,
        auth_error_code_provider: Callable[[], str] | None = None,
    ) -> None:
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.config_path = root / "auth_recovery_config.json"
        self.state_path = root / "auth_recovery_state.json"
        self.secret_path = root / "auth_recovery_secret.key"
        self.legacy_config_path = root / "grade_tracking_config.json"
        self.mail_service = mail_service
        self.logger = logger
        self.qr_login_starter = qr_login_starter
        self.pending_sms_provider = pending_sms_provider
        self.auth_committer = auth_committer
        self.remote_guard = remote_guard or nullcontext
        self.auth_error_code_provider = auth_error_code_provider
        self._lock = threading.RLock()
        self._flows: dict[str, dict[str, Any]] = {}
        self._callbacks: dict[str, list[Callable[[str], None]]] = {}
        self._config = self._load_config_with_migration()
        stored = self._read_json(self.state_path, {"contexts": []}).get("contexts", [])
        self._contexts: dict[str, dict[str, Any]] = {
            str(item["context_id"]): dict(item)
            for item in stored if isinstance(item, dict) and item.get("context_id")
        } if isinstance(stored, list) else {}
        self._secret = self._load_secret()
        with self._lock:
            if self._prune_contexts_locked():
                self._save_state_locked(prune=False)
        self.mail_service.register_materializer("auth_recovery", self.materialize_mail)

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

    def _load_config_with_migration(self) -> dict[str, Any]:
        current = self._read_json(self.config_path, {})
        if not current:
            legacy = self._read_json(self.legacy_config_path, {})
            current = {"public_base_url": str(legacy.get("site_url") or "").strip()}
            self._write_json(self.config_path, current)
        return {**DEFAULT_RECOVERY_CONFIG, **current}

    def _load_secret(self) -> bytes:
        try:
            value = self.secret_path.read_text(encoding="ascii").strip()
            if len(value) >= 43:
                return value.encode("ascii")
        except OSError:
            pass
        value = secrets.token_urlsafe(48)
        self.secret_path.write_text(value, encoding="ascii")
        secure_file(self.secret_path)
        return value.encode("ascii")

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._config)

    def update_config(self, values: dict[str, Any]) -> dict[str, Any]:
        url = str(values.get("public_base_url") or "").strip()
        if len(url) > 500:
            raise ValueError("重新登录地址过长")
        if url and not url.lower().startswith(("http://", "https://")):
            raise ValueError("重新登录地址必须以 http:// 或 https:// 开头")
        with self._lock:
            changed = url != self._config.get("public_base_url")
            self._config = {"public_base_url": url}
            self._write_json(self.config_path, self._config)
            if changed:
                self._invalidate_locked()
        if changed:
            self.mail_service.discard(kind="auth_recovery")
        return self.get_config()

    def register_recovered_callback(self, source: str, callback: Callable[[str], None]) -> None:
        with self._lock:
            self._callbacks.setdefault(str(source), []).append(callback)

    def stop(self) -> None:
        with self._lock:
            for context_id in list(self._flows):
                self._cancel_flow_locked(context_id)

    @staticmethod
    def _append_html_notice(html_body: str, message: str, *, url: str = "") -> str:
        if not html_body:
            return ""
        link = (
            f'<br><a href="{html.escape(url, quote=True)}" style="color:#1769e0">打开一次性登录页面</a>'
            if url else ""
        )
        notice = (
            '<div style="margin:18px 0 0;padding:14px 16px;border-radius:10px;'
            'background:#fff7e6;color:#7a4b00;line-height:1.7">'
            f'{html.escape(message)}{link}</div>'
        )
        return html_body.replace("</body>", notice + "</body>", 1) if "</body>" in html_body else html_body + notice

    @staticmethod
    def _call_target(callback: Callable[..., Any], target: str) -> Any:
        return callback(target) if target == "jwxk" else callback()

    def _signature(self, context_id: str) -> str:
        return hmac.new(self._secret, context_id.encode("ascii"), hashlib.sha256).hexdigest()

    def _token(self, context: dict[str, Any]) -> str:
        return f"{context['context_id']}.{self._signature(str(context['context_id']))}"

    def _token_hash(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _context_for_token(self, token: str) -> dict[str, Any]:
        token = str(token)
        if len(token) > 128:
            raise ValueError("一次性登录链接不存在或已失效")
        context_id, separator, signature = str(token).partition(".")
        if (
            not separator
            or len(context_id) != 32
            or any(character not in "0123456789abcdef" for character in context_id)
            or len(signature) != 64
            or any(character not in "0123456789abcdef" for character in signature)
        ):
            raise ValueError("一次性登录链接不存在或已失效")
        if not secrets.compare_digest(self._signature(context_id), signature):
            raise ValueError("一次性登录链接不存在或已失效")
        with self._lock:
            context = self._contexts.get(context_id)
            if (
                not context
                or context.get("status") != "active"
                or not secrets.compare_digest(
                    str(context.get("token_hash") or ""), self._token_hash(token),
                )
            ):
                raise ValueError("一次性登录链接不存在或已失效")
            return context

    @staticmethod
    def _context_time(context: dict[str, Any]) -> datetime:
        for key in ("completed_at", "cancelled_at", "invalidated_at", "created_at"):
            try:
                return datetime.fromisoformat(str(context.get(key) or ""))
            except (TypeError, ValueError):
                continue
        return datetime.min.replace(tzinfo=CHINA_TZ)

    def _prune_contexts_locked(self) -> bool:
        """Bound terminal recovery history without touching active links."""
        original_ids = set(self._contexts)
        cutoff = datetime.now(CHINA_TZ) - TERMINAL_CONTEXT_RETENTION
        active = {
            context_id: context for context_id, context in self._contexts.items()
            if context.get("status") == "active"
        }
        terminal = [
            (context_id, context)
            for context_id, context in self._contexts.items()
            if context.get("status") != "active"
            and self._context_time(context).astimezone(CHINA_TZ) >= cutoff
        ]
        terminal.sort(key=lambda item: self._context_time(item[1]), reverse=True)
        remaining = max(0, MAX_STORED_CONTEXTS - len(active))
        self._contexts = {**active, **dict(terminal[:remaining])}
        return set(self._contexts) != original_ids

    def _save_state_locked(self, *, prune: bool = True) -> None:
        if prune:
            self._prune_contexts_locked()
        self._write_json(self.state_path, {"contexts": list(self._contexts.values())})

    def _auth_error_code(self) -> str:
        if self.auth_error_code_provider is None:
            return ""
        try:
            return str(self.auth_error_code_provider() or "")
        except Exception:
            return ""

    def request_notification(
        self,
        *,
        source: str,
        target_service: str,
        account_id: str,
        subject: str,
        body: str,
        dedupe_key: str,
        html_body: str = "",
    ) -> bool:
        source = "course_selection" if source == "course_selection" else "grade_tracking"
        target = "jwxk" if target_service == "jwxk" else "primary"
        if not self.mail_service.is_configured():
            return False
        with self._lock:
            base_url = str(self._config.get("public_base_url") or "").strip()
            context = next((
                item for item in self._contexts.values()
                if item.get("status") == "active"
                and item.get("account_id") == str(account_id)
                and item.get("source") == source
                and item.get("target_service") == target
                and item.get("dedupe_key") == str(dedupe_key)
            ), None)
            if context is not None and context.get("notification_queued_at"):
                return True
            if context is None:
                context_id = uuid.uuid4().hex
                context = {
                    "context_id": context_id,
                    "account_id": str(account_id),
                    "source": source,
                    "target_service": target,
                    "created_at": _iso(),
                    "dedupe_key": str(dedupe_key),
                    "status": "active",
                }
                context["token_hash"] = self._token_hash(self._token(context))
                self._contexts[context_id] = context
                self._save_state_locked()
            context_id = str(context["context_id"])
        delivery_mode = "link"
        continued_sms = False
        if self._auth_error_code() == "WEBVPN_CAMPUS_NETWORK_BLOCKED":
            delivery_mode = "campus_network"
        elif not base_url:
            delivery_mode = "manual"
        else:
            continued_sms = self._adopt_pending_sms(context_id, target)
        queued = self.mail_service.queue_notification(
            source,
            subject,
            body,
            f"auth-recovery:{source}:{dedupe_key}",
            html_body,
            kind="auth_recovery",
            template_metadata={
                "context_id": context_id,
                "continued_sms": continued_sms,
                "delivery_mode": delivery_mode,
            },
        )
        if queued:
            with self._lock:
                current = self._contexts.get(context_id)
                if current and current.get("status") == "active":
                    current["notification_queued_at"] = _iso()
                    self._save_state_locked()
        return queued

    def materialize_mail(self, message: dict[str, Any]) -> dict[str, str] | None:
        metadata = message.get("template_metadata") or {}
        context_id = str(metadata.get("context_id") or "")
        with self._lock:
            context = self._contexts.get(context_id)
            base_url = str(self._config.get("public_base_url") or "").strip()
            if not context or context.get("status") != "active":
                return None
            token = self._token(context) if base_url else ""
        delivery_mode = str(metadata.get("delivery_mode") or "link")
        if delivery_mode == "campus_network":
            notice = "学校 WebVPN 当前拒绝校园网访问。请进入工具箱切换为“校内直连”并重新登录。"
            return {
                "subject": str(message.get("subject") or ""),
                "body": str(message.get("body") or "") + "\n\n" + notice,
                "html_body": self._append_html_notice(str(message.get("html_body") or ""), notice),
            }
        if delivery_mode == "manual" or not base_url:
            notice = "当前未配置可从邮件访问的重新登录地址，请进入 NEU 教务工具箱手动登录。"
            return {
                "subject": str(message.get("subject") or ""),
                "body": str(message.get("body") or "") + "\n\n" + notice,
                "html_body": self._append_html_notice(str(message.get("html_body") or ""), notice),
            }
        url = f"{base_url.rstrip('/')}/auth-recovery/{token}"
        continued = bool(metadata.get("continued_sms"))
        intro = (
            "后台已使用保存的账号信息完成第一步登录，请打开一次性页面直接填写图形验证码并完成短信验证码。"
            if continued else
            "请打开一次性页面恢复登录；页面会先提供微信扫码，如学校要求二次认证，可在同一页面继续完成图形验证码和短信验证码。"
        )
        suffix = (
            f"\n\n{intro}\n{url}\n\n链接在成功建立会话后失效。"
            "二维码或短信流程过期后可在同一链接重新开始；验证码不会由后台自动填写或发送，请勿转发。"
        )
        return {
            "subject": str(message.get("subject") or ""),
            "body": str(message.get("body") or "") + suffix,
            "html_body": self._append_html_notice(str(message.get("html_body") or ""), intro, url=url),
        }

    def _adopt_pending_sms(self, context_id: str, target: str) -> bool:
        if self.pending_sms_provider is None:
            return False
        with self._lock:
            existing = self._flows.get(context_id)
            if existing:
                return existing.get("flow", {}).get("kind") == "sms"
        candidate = self._call_target(self.pending_sms_provider, target)
        if not candidate:
            return False
        client, flow = candidate
        if not client or not flow or flow.get("status") != "sms_required":
            return False
        if str(flow.get("target_service") or "primary") != target:
            return False
        with self._lock:
            self._flows[context_id] = {
                "client": client,
                "flow": self._flow_payload("sms", {**flow, "target_service": target}),
            }
        return True

    @staticmethod
    def _flow_payload(kind: str, flow: dict[str, Any]) -> dict[str, Any]:
        payload = {"kind": kind, **flow}
        try:
            expires_in = max(1, int(payload.get("expires_in", 300)))
        except (TypeError, ValueError):
            expires_in = 300
        payload.update(expires_in=expires_in, expires_at=time.time() + expires_in)
        return payload

    @staticmethod
    def _update_flow(flow: dict[str, Any], result: dict[str, Any]) -> None:
        for key in ("captcha_image", "expires_in"):
            if key in result:
                flow[key] = result[key]
        if "expires_in" in result:
            try:
                expires_in = max(1, int(result.get("expires_in") or 300))
            except (TypeError, ValueError):
                expires_in = 300
            flow.update(expires_in=expires_in, expires_at=time.time() + expires_in)

    def get_status(self, token: str) -> dict[str, Any]:
        context = self._context_for_token(token)
        with self._lock:
            record = self._flows.get(str(context["context_id"]))
            if not record:
                return {"status": "ready"}
            flow = record["flow"]
            status = "sms_required" if flow.get("kind") == "sms" else "qr_pending"
            expires_in = max(0, int(float(flow.get("expires_at", 0)) - time.time()))
            return {"status": status, "expires_in": expires_in, **{
                key: value for key, value in flow.items()
                if key in {"flow_id", "qr_content", "poll_interval", "captcha_image"}
            }}

    def start(self, token: str) -> dict[str, Any]:
        context = self._context_for_token(token)
        context_id = str(context["context_id"])
        target = str(context.get("target_service") or "primary")
        with self._lock:
            self._cancel_flow_locked(context_id)
            if self._adopt_pending_sms(context_id, target):
                flow = self._flows[context_id]["flow"]
                return {"status": "sms_required", **{
                    key: value for key, value in flow.items()
                    if key in {"flow_id", "captcha_image", "expires_in"}
                }}
            if not self.qr_login_starter:
                raise RuntimeError("当前运行环境不支持二维码恢复")
            with self.remote_guard():
                client, flow = self._call_target(self.qr_login_starter, target)
            self._flows[context_id] = {
                "client": client,
                "flow": self._flow_payload("qr", {**flow, "target_service": target}),
            }
            return {"status": "pending", **flow}

    def poll(self, token: str) -> dict[str, Any]:
        context = self._context_for_token(token)
        context_id = str(context["context_id"])
        with self._lock:
            record = self._flows.get(context_id)
            if not record:
                return {"status": "not_started"}
            flow = record["flow"]
            if flow.get("kind") == "sms":
                return {"status": "sms_required", **{
                    key: value for key, value in flow.items()
                    if key in {"flow_id", "captcha_image", "expires_in"}
                }}
            try:
                with self.remote_guard():
                    result = record["client"].poll_webvpn_qr_login(flow["flow_id"])
            except Exception:
                self.logger.warning("[远程认证恢复] 二维码轮询失败", exc_info=True)
                return {"status": "pending", "message": "状态查询暂时失败，正在重试"}
            if result.get("status") == "authenticated":
                return self._complete_locked(context, record["client"])
            if result.get("status") == "sms_required":
                target = str(context.get("target_service") or "primary")
                record["flow"] = self._flow_payload("sms", {**result, "target_service": target})
            elif result.get("status") in {"expired", "error"}:
                self._flows.pop(context_id, None)
            return result

    def _require_sms(self, token: str) -> tuple[dict[str, Any], Any, dict[str, Any]]:
        context = self._context_for_token(token)
        record = self._flows.get(str(context["context_id"]))
        if not record or record["flow"].get("kind") != "sms":
            raise ValueError("短信验证流程不存在，请重新开始登录")
        return context, record["client"], record["flow"]

    def refresh_captcha(self, token: str) -> dict[str, Any]:
        with self._lock:
            _context, client, flow = self._require_sms(token)
            with self.remote_guard():
                result = client.refresh_webvpn_captcha(flow["flow_id"])
            self._update_flow(flow, result)
            return {"success": True, **result}

    def send_sms(self, token: str, captcha_code: str) -> dict[str, Any]:
        with self._lock:
            _context, client, flow = self._require_sms(token)
            with self.remote_guard():
                result = client.send_webvpn_sms_code(flow["flow_id"], captcha_code)
            self._update_flow(flow, result)
            return {"success": result.get("status") == "sent", **result}

    def verify_sms(self, token: str, code: str, trust_device: bool = False) -> dict[str, Any]:
        with self._lock:
            context, client, flow = self._require_sms(token)
            with self.remote_guard():
                result = client.verify_webvpn_sms_code(flow["flow_id"], code, trust_device)
            if result.get("status") == "authenticated":
                return self._complete_locked(context, client)
            self._update_flow(flow, result)
            return {"success": False, **result}

    def cancel(self, token: str) -> dict[str, Any]:
        context = self._context_for_token(token)
        with self._lock:
            self._cancel_flow_locked(str(context["context_id"]))
            context["status"] = "cancelled"
            context["cancelled_at"] = _iso()
            self._save_state_locked()
        return {"success": True, "status": "cancelled"}

    def _complete_locked(self, context: dict[str, Any], client: Any) -> dict[str, Any]:
        target = str(context.get("target_service") or "primary")
        if self.auth_committer:
            self.auth_committer(client, target) if target == "jwxk" else self.auth_committer(client)
        username = str(getattr(client, "username", "") or context.get("account_id") or "") or None
        source = str(context.get("source") or "")
        self._flows.pop(str(context["context_id"]), None)
        context["status"] = "completed"
        context["completed_at"] = _iso()
        self._save_state_locked()
        for callback in self._callbacks.get(source, []):
            try:
                callback(str(username or ""))
            except Exception:
                self.logger.exception("[远程认证恢复] 恢复完成回调失败")
        return {"status": "authenticated", "username": username}

    def _cancel_flow_locked(self, context_id: str) -> None:
        record = self._flows.pop(context_id, None)
        if not record:
            return
        try:
            with self.remote_guard():
                flow = record["flow"]
                if flow.get("kind") == "sms":
                    record["client"].cancel_webvpn_sms_login(flow.get("flow_id"))
                else:
                    record["client"].cancel_webvpn_qr_login(flow.get("flow_id"))
        except Exception:
            self.logger.debug("[远程认证恢复] 取消旧流程失败", exc_info=True)

    def invalidate_all(self, *, clear_state: bool = False) -> None:
        with self._lock:
            for context_id in list(self._flows):
                self._cancel_flow_locked(context_id)
            if clear_state:
                self._contexts.clear()
            else:
                for context in self._contexts.values():
                    if context.get("status") == "active":
                        context["status"] = "invalidated"
                        context["invalidated_at"] = _iso()
            self._save_state_locked()
        self.mail_service.discard(kind="auth_recovery")

    def invalidate_matching(
        self,
        *,
        source: str = "",
        target_service: str = "",
        account_id: str = "",
    ) -> int:
        """Invalidate one caller's active recovery episode without touching others."""
        source = str(source or "")
        target_service = str(target_service or "")
        account_id = str(account_id or "")
        with self._lock:
            context_ids = {
                context_id
                for context_id, context in self._contexts.items()
                if context.get("status") == "active"
                and (not source or context.get("source") == source)
                and (
                    not target_service
                    or context.get("target_service") == target_service
                )
                and (not account_id or context.get("account_id") == account_id)
            }
            for context_id in context_ids:
                self._cancel_flow_locked(context_id)
                context = self._contexts[context_id]
                context["status"] = "invalidated"
                context["invalidated_at"] = _iso()
            if context_ids:
                self._save_state_locked()
        if context_ids:
            self.mail_service.discard(
                kind="auth_recovery",
                template_context_ids=context_ids,
            )
        return len(context_ids)

    def _invalidate_locked(self) -> None:
        for context_id in list(self._flows):
            self._cancel_flow_locked(context_id)
        for context in self._contexts.values():
            if context.get("status") == "active":
                context["status"] = "invalidated"
                context["invalidated_at"] = _iso()
        self._save_state_locked()
