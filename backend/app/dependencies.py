"""
全局状态管理与依赖注入

所有全局单例状态集中在此，供 routers 通过 FastAPI Depends 使用。
"""

import os
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Optional

from backend.core.auth import AuthSessionManager, NEUAuthClient
from backend.core.storage import (
    AcademicReportStorage,
    AutoLoginManager,
    ResearchTrainingStorage,
    Storage,
)
from backend.core.log import (
    LogCategory,
    LogConfig,
    LogLevel,
    get_logger,
    log_security_event,
    setup_logging,
)
from backend.core.log.manager import LogManager
from backend.core.tracking import GradeTrackingService
from backend.core.cache import (
    AccountScope,
    CacheCoordinator,
    CacheRegistry,
    CacheResourceSpec,
    CacheStore,
    PayloadType,
)
from backend.core.cache.resources import (
    avatar_payload,
    avatar_token,
    canonicalize_academic_report,
    canonicalize_avatar,
    canonicalize_research_training,
    canonicalize_scores,
    canonicalize_score_detail,
    diff_academic_report,
    diff_avatar,
    diff_research_training,
    diff_scores,
    diff_score_detail,
    fetch_academic_report,
    fetch_research_training,
    fetch_scores,
    fetch_festival_activities,
    canonicalize_festival_activities,
    diff_festival_activities,
    score_detail_variant,
)

# ── 全局状态 ──────────────────────────────────────────────────────────────────

_auth_sessions = AuthSessionManager()


@contextmanager
def remote_session_guard():
    """Serialize every operation that may touch the shared remote session."""
    with _auth_sessions.remote_guard():
        yield


_storage = Storage()

# Cookie 持久化文件路径
COOKIE_FILE = os.path.join(_storage.config.data_dir, "session.json")

# 初始化日志系统
_log_config = LogConfig(
    log_dir=os.path.join(_storage.config.data_dir, "logs"),
    level=LogLevel.INFO,
    console_output=True,
)
setup_logging(_log_config)

# 初始化自动登录管理器
_auto_login = AutoLoginManager(_storage, cookie_file=COOKIE_FILE)

# 初始化日志管理器
_log_manager = LogManager(_log_config)

# API 错误日志记录器
_api_logger = get_logger("api", LogCategory.SYSTEM, _log_config)

# 初始化培养计划存储
_report_storage = AcademicReportStorage(_storage)
_research_storage = ResearchTrainingStorage(_storage.config.data_dir)


def _cache_client(context):
    """Resolve a cache fetch against the exact identity captured at submission."""
    with _auth_sessions.identity_commit_guard():
        client = _auth_sessions.peek_client()
        if (
            client is None
            or _auth_sessions.epoch() != context.identity_epoch
            or str(getattr(client, "username", "") or "") != context.key.account_id
            or not getattr(client, "is_logged_in", False)
        ):
            raise RuntimeError("cache identity is no longer active")
        return client


def _fetch_scores_resource(context):
    return fetch_scores(_cache_client(context))


def _fetch_score_detail_resource(context):
    from backend.core.cache import CacheFetchSkipped, CacheKey

    scores_entry = _cache_store.get(CacheKey(context.key.account_id, "scores"))
    if scores_entry is None or not isinstance(scores_entry.payload, dict):
        raise RuntimeError("成绩总表缓存不可用")
    source = None
    for score in scores_entry.payload.get("scores") or []:
        if not isinstance(score, dict):
            continue
        if score_detail_variant(
            str(score.get("code") or ""),
            str(score.get("term") or ""),
        ) == context.key.variant:
            source = score
            break
    if source is None:
        raise RuntimeError("课程已不在当前成绩总表中")
    detail_ref = str(source.get("detail_ref") or "")
    if not detail_ref:
        raise RuntimeError("课程详情标识不可用，请先刷新总成绩")
    detail = _cache_client(context).academic.get_score_detail(detail_ref)
    items = [
        {
            "code": item.code,
            "name": item.name,
            "value": item.value,
            "pass": item.passed,
            "highest_score_in_proportion": item.highest_score_in_proportion,
        }
        for item in detail.items
    ]
    if not any(
        item["code"] or item["name"] or item["value"] not in (None, "")
        for item in items
    ):
        return CacheFetchSkipped("no_detail_data")
    return {
        "course_code": str(source.get("code") or ""),
        "term": str(source.get("term") or ""),
        "source_score": str(source.get("score") or ""),
        "source_gpa": source.get("gpa"),
        "score": detail.score,
        "grade_point": detail.grade_point,
        "pass": detail.passed,
        "item_scores": items,
    }


def _fetch_report_resource(context):
    return fetch_academic_report(
        _cache_client(context),
        _report_storage._report_to_dict,
    )


def _fetch_research_resource(context):
    return fetch_research_training(_cache_client(context))


def _fetch_festival_resource(context):
    return fetch_festival_activities(_cache_client(context))


def _fetch_avatar_resource(context):
    client = _cache_client(context)
    user_info = client.get_user_info()
    token = str((user_info or {}).get("avatar_token") or "")
    if not token:
        raise RuntimeError("当前账号没有头像")
    current = _cache_store.get(context.key)
    if current and current.payload_type == PayloadType.BLOB:
        try:
            if avatar_token(current.payload) == token:
                return current.payload
        except (TypeError, ValueError, UnicodeError):
            pass
    image = client.get_avatar(token)
    if not image:
        raise RuntimeError("头像下载失败")
    return avatar_payload(token, image)


_cache_registry = CacheRegistry(
    (
        CacheResourceSpec(
            resource="scores",
            schema_version=1,
            revision_algorithm_version=1,
            account_scope=AccountScope.ACCOUNT,
            payload_type=PayloadType.JSON,
            max_age=timedelta(minutes=5),
            offline_readable=True,
            sensitivity="private-academic",
            fetch=_fetch_scores_resource,
            canonicalize=canonicalize_scores,
            diff=diff_scores,
        ),
        CacheResourceSpec(
            resource="score-details",
            schema_version=1,
            revision_algorithm_version=1,
            account_scope=AccountScope.ACCOUNT,
            payload_type=PayloadType.JSON,
            max_age=timedelta(days=36500),
            offline_readable=True,
            sensitivity="private-academic",
            fetch=_fetch_score_detail_resource,
            canonicalize=canonicalize_score_detail,
            diff=diff_score_detail,
        ),
        CacheResourceSpec(
            resource="academic-report",
            schema_version=2,
            revision_algorithm_version=1,
            account_scope=AccountScope.ACCOUNT,
            payload_type=PayloadType.JSON,
            max_age=timedelta(minutes=5),
            offline_readable=True,
            sensitivity="private-academic",
            fetch=_fetch_report_resource,
            canonicalize=canonicalize_academic_report,
            diff=diff_academic_report,
            dependencies=("scores",),
        ),
        CacheResourceSpec(
            resource="research-training",
            schema_version=1,
            revision_algorithm_version=1,
            account_scope=AccountScope.ACCOUNT,
            payload_type=PayloadType.JSON,
            max_age=timedelta(minutes=5),
            offline_readable=True,
            sensitivity="private-academic",
            fetch=_fetch_research_resource,
            canonicalize=canonicalize_research_training,
            diff=diff_research_training,
            mutation_invalidations=("research-training",),
        ),
        CacheResourceSpec(
            resource="festival-activities",
            schema_version=1,
            revision_algorithm_version=1,
            account_scope=AccountScope.ACCOUNT,
            payload_type=PayloadType.JSON,
            max_age=timedelta(minutes=30),
            offline_readable=True,
            sensitivity="private-activity",
            fetch=_fetch_festival_resource,
            canonicalize=canonicalize_festival_activities,
            diff=diff_festival_activities,
        ),
        CacheResourceSpec(
            resource="avatar",
            schema_version=1,
            revision_algorithm_version=1,
            account_scope=AccountScope.ACCOUNT,
            payload_type=PayloadType.BLOB,
            max_age=timedelta(hours=24),
            offline_readable=False,
            sensitivity="private-profile",
            fetch=_fetch_avatar_resource,
            canonicalize=canonicalize_avatar,
            diff=diff_avatar,
            mutation_invalidations=("avatar",),
        ),
    )
)
_cache_store = CacheStore(Path(_storage.config.data_dir) / "cache.db")


@contextmanager
def _identity_commit_guard(account: str, epoch: int):
    # set_auth_client/logout/clear-data also take this lock. Coordinator validates
    # inside the guard immediately before commit, preventing stale jobs from
    # recreating data after an account switch.
    with _auth_sessions.identity_commit_guard():
        yield


@contextmanager
def _local_cache_import_guard(account: str):
    """Protect an account-bound, network-free legacy cache import."""
    with _auth_sessions.local_cache_import_guard(account):
        yield


_cache_coordinator = CacheCoordinator(
    _cache_store,
    _cache_registry,
    identity_validator=lambda account, epoch: auth_generation_is_current(
        epoch, account
    ),
    identity_commit_guard=_identity_commit_guard,
    remote_guard=remote_session_guard,
    worker_count=2,
    autostart=False,
)


def schedule_login_bootstrap(client: NEUAuthClient) -> None:
    """Resume user intent and warm the shared score resource after any login."""
    username = str(getattr(client, "username", "") or "")
    if not username or not getattr(client, "is_logged_in", False):
        return
    tracker = globals().get("_grade_tracker")
    if tracker and hasattr(tracker, "resume_after_login"):
        tracker.resume_after_login(username)
    try:
        _cache_coordinator.submit(
            account_id=username,
            resource="scores",
            identity_epoch=get_auth_generation(),
            force=False,
            reason="login_bootstrap",
        )
    except RuntimeError:
        # Normal during bounded application shutdown.
        pass


# ── 全局状态修改接口 ──────────────────────────────────────────────────────────

def set_auth_client(
    client: Optional[NEUAuthClient],
    *,
    force_epoch: bool = False,
):
    """设置当前认证客户端"""
    _auth_sessions.set_client(client, force_epoch=force_epoch)
    tracker = globals().get("_grade_tracker")
    if tracker and client is not None and client.is_logged_in:
        tracker.invalidate_recovery_link()


def logout_auth_client(*, clear_cache: bool) -> str | None:
    """Fence background work and optionally clear the active account cache."""
    def cleanup(account: str) -> None:
        _cache_coordinator.cancel_account(account, error_kind="identity_changed")
        if clear_cache:
            _cache_store.delete_account(account)
            _research_storage.delete_account(account)
    account = _auth_sessions.fence_and_clear(cleanup)
    tracker = globals().get("_grade_tracker")
    if tracker and hasattr(tracker, "pause_for_logout"):
        tracker.pause_for_logout(clear_personal_state=clear_cache)
    return account


def peek_auth_client() -> Optional[NEUAuthClient]:
    """Return the in-memory client without triggering a login attempt."""
    return _auth_sessions.peek_client()


def get_auth_generation() -> int:
    """Return the current identity epoch used to fence background commits."""
    return _auth_sessions.epoch()


def auth_generation_is_current(
    generation: int,
    username: str | None = None,
) -> bool:
    """Check that a background task still belongs to the active identity."""
    return _auth_sessions.is_current(generation, username)


# ── 依赖函数 ──────────────────────────────────────────────────────────────────

def _get_auth_client_unlocked() -> Optional[NEUAuthClient]:
    """
    获取当前认证客户端

    恢复优先级：
    1. 内存中的客户端（如果有效）
    2. 尝试用保存的 Cookie 恢复（免密）
    3. 尝试用保存的密码重新登录
    """
    attempted_password_login = False

    # 1. 检查内存中的客户端
    active_client = _auth_sessions.peek_client()
    if active_client is not None:
        # 二维码或短信流程必须继续使用原 Session。状态查询不能在流程尚未
        # 完成时触发另一轮静默账密登录并覆盖 flow。
        if active_client._webvpn_qr_flow or active_client._webvpn_sms_flow:
            return active_client if active_client.is_logged_in else None

        # 尝试确保登录（内部会优先用 Cookie 刷新）
        attempted_password_login = bool(
            getattr(active_client, "username", "")
            and getattr(active_client, "password", "")
        )
        try:
            if active_client.ensure_login():
                return active_client
        except Exception as error:
            _api_logger.warning(
                "[Auth] 当前会话自动恢复失败: %s",
                type(error).__name__,
            )
            log_security_event(
                "neu_session_restore",
                "failure",
                subject=getattr(active_client, "username", ""),
                reason="active_session_restore_failed",
                auth_method="session_cookie",
                error_type=type(error).__name__,
            )
        set_auth_client(None)

    # 2. 先尝试恢复二维码/WebVPN Cookie 会话，不要求保存密码
    session_client = NEUAuthClient(cookie_file=COOKIE_FILE)
    try:
        if session_client.ensure_login():
            set_auth_client(session_client)
            schedule_login_bootstrap(session_client)
            log_security_event(
                "neu_session_restore",
                "success",
                subject=getattr(session_client, "username", ""),
                auth_method="session_cookie",
                network_mode=getattr(session_client, "active_mode", ""),
            )
            return session_client
    except Exception as error:
        _api_logger.warning(
            "[Auth] Cookie 会话自动恢复失败: %s",
            type(error).__name__,
        )
        log_security_event(
            "neu_session_restore",
            "failure",
            reason="stored_session_restore_failed",
            auth_method="session_cookie",
            error_type=type(error).__name__,
        )

    # 3. 尝试加载保存的凭证并创建客户端
    creds = _storage.load_credentials()
    if creds and not attempted_password_login:
        username, password = creds
        # 创建客户端时会自动尝试从 Cookie 文件恢复
        client = NEUAuthClient(
            username=username,
            password=password,
            cookie_file=COOKIE_FILE
        )
        # 尝试登录（内部会优先用 Cookie 刷新票据）
        try:
            if client.ensure_login():
                set_auth_client(client)
                schedule_login_bootstrap(client)
                log_security_event(
                    "neu_session_restore",
                    "success",
                    subject=username,
                    auth_method="stored_credentials",
                    network_mode=getattr(client, "active_mode", ""),
                )
                return client
        except Exception as error:
            _api_logger.warning(
                "[Auth] 已保存账号密码自动恢复失败: %s",
                type(error).__name__,
            )
            log_security_event(
                "neu_session_restore",
                "failure",
                subject=username,
                reason="stored_credentials_restore_failed",
                auth_method="stored_credentials",
                error_type=type(error).__name__,
            )

    return None


def get_auth_client() -> Optional[NEUAuthClient]:
    """Resolve the current client while holding the shared session boundary."""
    with remote_session_guard():
        return _get_auth_client_unlocked()


def _start_tracking_qr_login():
    client = NEUAuthClient(
        cookie_file=COOKIE_FILE,
        network_mode="webvpn",
        restore_session=False,
    )
    return client, client.start_webvpn_qr_login(expires_in=300)


def _interactive_login_pending() -> bool:
    client = _auth_sessions.peek_client()
    return bool(
        client
        and (client._webvpn_qr_flow or client._webvpn_sms_flow)
    )


def _tracking_score_refresh(account: str, manual: bool) -> dict:
    submission = _cache_coordinator.submit(
        account_id=account,
        resource="scores",
        identity_epoch=get_auth_generation(),
        force=manual,
        reason="manual" if manual else "tracking",
    )
    if submission.job_id:
        import time
        from backend.core.cache import JobStatus

        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            job = _cache_coordinator.get_job(submission.job_id)
            if job and job.status in {
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                if job.status != JobStatus.COMPLETED:
                    raise RuntimeError(job.error_kind or "成绩刷新失败")
                break
            time.sleep(0.05)
    entry, _stale = _cache_coordinator.read(
        account_id=account,
        resource="scores",
    )
    if entry is None:
        raise RuntimeError("成绩刷新完成但缓存不可用")
    return {
        "revision": entry.revision,
        "payload": entry.payload,
    }


def _tracking_score_detail_lookup(account: str, score: dict) -> dict:
    """Fetch one changed course detail before composing a tracking email."""
    import time
    from backend.core.cache import CacheKey, JobStatus

    variant = score_detail_variant(
        str(score.get("code") or ""),
        str(score.get("term") or ""),
    )
    submission = _cache_coordinator.submit(
        account_id=account,
        resource="score-details",
        variant=variant,
        identity_epoch=get_auth_generation(),
        force=True,
        reason="tracking",
    )
    job = None
    if submission.job_id:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = _cache_coordinator.get_job(submission.job_id)
            if job and job.status in {
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                break
            time.sleep(0.05)
    if job is None or job.status != JobStatus.COMPLETED:
        return {"status": "failed"}
    if job.changes.get("skipped"):
        return {"status": "no_data"}
    entry = _cache_store.get(CacheKey(account, "score-details", variant))
    if entry is None or not isinstance(entry.payload, dict):
        return {"status": "failed"}
    return {
        "status": "available",
        "item_scores": list(entry.payload.get("item_scores") or []),
    }


_grade_tracker = GradeTrackingService(
    data_dir=_storage.config.data_dir,
    auth_provider=get_auth_client,
    score_storage=_storage,
    report_storage=_report_storage,
    logger=_api_logger,
    qr_login_starter=_start_tracking_qr_login,
    auth_setter=set_auth_client,
    login_flow_pending=_interactive_login_pending,
    score_refresher=_tracking_score_refresh,
    score_detail_lookup=_tracking_score_detail_lookup,
    remote_guard=remote_session_guard,
)


def _handle_cache_event(event) -> None:
    account = event.key.account_id
    if event.key.resource == "research-training" and event.changed:
        entry = _cache_store.get(event.key)
        if entry and isinstance(entry.payload, dict):
            _research_storage.sync_favorite_archives(account, entry.payload)
        return
    if event.key.resource != "scores" or not event.changed:
        return
    entry = _cache_store.get(event.key)
    if entry is None or not isinstance(entry.payload, dict):
        return
    _grade_tracker.handle_scores_revision(
        account,
        entry.revision,
        entry.payload,
        reason=event.reason,
    )
    if event.reason == "legacy_migration":
        return
    client = peek_auth_client()
    if (
        client
        and getattr(client, "is_logged_in", False)
        and str(getattr(client, "username", "") or "") == account
    ):
        try:
            _cache_coordinator.submit(
                account_id=account,
                resource="academic-report",
                identity_epoch=get_auth_generation(),
                force=False,
                reason="page_swr",
            )
        except RuntimeError:
            pass


_cache_event_unsubscribe = _cache_coordinator.add_event_listener(
    _handle_cache_event
)


def require_auth() -> NEUAuthClient:
    """需要登录的依赖"""
    client = get_auth_client()
    if client is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return client


def require_serialized_auth():
    """Hold exclusive access to the shared requests.Session for a remote route."""
    with remote_session_guard():
        client = _get_auth_client_unlocked()
        if client is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="未登录或登录已过期")
        yield client


def require_cached_auth_identity() -> NEUAuthClient:
    """Identify the current account without a remote session health check."""
    client = peek_auth_client()
    if (
        client is None
        or not getattr(client, "username", None)
        or not getattr(client, "is_logged_in", False)
    ):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="未登录或无法确认当前账号")
    return client


# ── GPA 模拟工具函数 ──────────────────────────────────────────────────────────

def get_gpa_simulation_dir():
    """获取GPA模拟文件存储目录"""
    sim_dir = os.path.join(_storage.config.data_dir, "成绩")
    os.makedirs(sim_dir, exist_ok=True)
    return sim_dir
