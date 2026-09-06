"""Single-course-at-a-time metadata synchronization."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import timedelta
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.core.cache.models import utc_now


def course_variant(course_code: str) -> str:
    return f"course:{course_code}"


def metadata_needs_sync(entry, stale: bool) -> bool:
    if entry is None or stale:
        return True
    status = entry.payload.get("status")
    if status == "not_found":
        return not entry.last_checked_at or entry.last_checked_at + timedelta(days=7) <= utc_now()
    return status != "success"


def plan_fingerprint(course: dict[str, Any]) -> str:
    stable = {
        "course_code": course.get("course_code") or course.get("code") or "",
        "course_name": course.get("course_name") or course.get("name") or "",
        "credit": course.get("credit"),
        "course_nature": course.get("course_nature") or course.get("course_type") or "",
        "plan_term": course.get("plan_term") or course.get("suggest_term") or course.get("term_code") or "",
    }
    return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@dataclass
class SyncState:
    account: str = ""
    running: bool = False
    cancelled: bool = False
    total: int = 0
    completed: int = 0
    failed: int = 0
    current_course: str = ""
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "errors": list(self.errors)}


class CourseOutlineMetadataSyncService:
    def __init__(self, *, cache_store, cache_coordinator, auth_epoch: Callable[[], int]):
        self.store = cache_store
        self.coordinator = cache_coordinator
        self.auth_epoch = auth_epoch
        self._lock = threading.RLock()
        self._state = SyncState()
        self._epoch: int | None = None
        self._thread: threading.Thread | None = None

    def start(self, account: str, courses: list[dict[str, Any]], *, force: bool = False) -> dict[str, Any]:
        epoch = self.auth_epoch()
        with self._lock:
            if self._thread and self._thread.is_alive():
                if self._state.account == account and self._epoch == epoch:
                    return {**self._state.as_dict(), "accepted": False}
                self._state.cancelled = True
            ordered = sorted(courses, key=self._priority)
            self._state = SyncState(account=account, running=True, total=len(ordered))
            self._epoch = epoch
            self._thread = threading.Thread(
                target=self._run, args=(account, ordered, force, self._state, epoch), daemon=True,
                name="course-outline-metadata-sync",
            )
            try:
                self._thread.start()
            except Exception:
                self._state.running = False
                raise
            return {**self._state.as_dict(), "accepted": True}

    def cancel(self, account: str) -> dict[str, Any]:
        with self._lock:
            if self._state.account == account:
                self._state.cancelled = True
            return self.status(account)

    def status(self, account: str) -> dict[str, Any]:
        with self._lock:
            return self._state.as_dict() if self._state.account == account else SyncState(account=account).as_dict()

    @staticmethod
    def _priority(course: dict[str, Any]) -> tuple[int, str]:
        passed = course.get("is_passed") in (True, "是")
        score = course.get("score")
        selected = course.get("is_selected") in (True, "是") or "已选" in str(course.get("status") or "")
        bucket = 0 if not passed and not score else 1 if selected and not score else 2
        return bucket, str(course.get("course_code") or "")

    def _interrupted(self, state: SyncState, epoch: int) -> bool:
        changed = self.auth_epoch() != epoch
        with self._lock:
            state.cancelled = state.cancelled or changed
            return state.cancelled

    def _run(
        self, account: str, courses: list[dict[str, Any]], force: bool,
        state: SyncState | None = None, identity_epoch: int | None = None,
    ) -> None:
        # Each worker owns its state so an old identity cannot finish a new batch.
        state = state or self._state
        try:
            epoch = self.auth_epoch() if identity_epoch is None else identity_epoch
            for course in courses:
                if self._interrupted(state, epoch):
                    break
                with self._lock:
                    code = str(course.get("course_code") or course.get("code") or "").strip()
                    state.current_course = code
                if not code:
                    with self._lock:
                        state.completed += 1
                    continue
                try:
                    current, stale = self.coordinator.read(
                        account_id=account, resource="course-outline-metadata", variant=course_variant(code),
                    )
                    fingerprint = plan_fingerprint(course)
                    if (
                        not force and not metadata_needs_sync(current, stale)
                        and current.payload.get("plan_fingerprint") == fingerprint
                    ):
                        with self._lock:
                            state.completed += 1
                        continue
                    submission = self.coordinator.submit(
                        account_id=account,
                        resource="course-outline-metadata",
                        variant=course_variant(code),
                        identity_epoch=epoch,
                        force=True,
                        reason=f"metadata_sync:{fingerprint}",
                    )
                    job = None
                    if submission.job_id:
                        deadline = time.monotonic() + 60
                        while time.monotonic() < deadline:
                            if self._interrupted(state, epoch):
                                break
                            job = self.coordinator.get_job(submission.job_id)
                            if job is None or getattr(job.status, "value", "") in {
                                "completed", "failed", "cancelled"
                            }:
                                break
                            time.sleep(0.1)
                    if self._interrupted(state, epoch):
                        break
                    with self._lock:
                        if job is not None and getattr(job.status, "value", "") == "completed":
                            state.completed += 1
                        else:
                            state.failed += 1
                            state.errors.append(code)
                except Exception:
                    # One unavailable outline must not abort the batch or leave
                    # the public status permanently stuck in ``running``.
                    with self._lock:
                        state.failed += 1
                        state.errors.append(code)
        finally:
            with self._lock:
                state.running = False
                state.current_course = ""
