"""Single-course-at-a-time metadata synchronization."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import timedelta
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.core.cache import CacheKey
from backend.core.cache.models import utc_now


def course_variant(course_code: str) -> str:
    return f"course:{course_code}"


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
        return {**self.__dict__, "errors": list(self.errors[-10:])}


class CourseOutlineMetadataSyncService:
    def __init__(self, *, cache_store, cache_coordinator, auth_epoch: Callable[[], int]):
        self.store = cache_store
        self.coordinator = cache_coordinator
        self.auth_epoch = auth_epoch
        self._lock = threading.RLock()
        self._state = SyncState()
        self._thread: threading.Thread | None = None

    def start(self, account: str, courses: list[dict[str, Any]], *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self._state.as_dict()
            ordered = sorted(courses, key=self._priority)
            self._state = SyncState(account=account, running=True, total=len(ordered))
            self._thread = threading.Thread(
                target=self._run, args=(account, ordered, force), daemon=True,
                name="course-outline-metadata-sync",
            )
            self._thread.start()
            return self._state.as_dict()

    def cancel(self, account: str) -> dict[str, Any]:
        with self._lock:
            if self._state.account == account:
                self._state.cancelled = True
            return self._state.as_dict()

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

    def _run(self, account: str, courses: list[dict[str, Any]], force: bool) -> None:
        for course in courses:
            with self._lock:
                if self._state.cancelled:
                    break
                code = str(course.get("course_code") or course.get("code") or "").strip()
                self._state.current_course = code
            if not code:
                continue
            current = self.store.get(CacheKey(account, "course-outline-metadata", course_variant(code)))
            fingerprint = plan_fingerprint(course)
            reusable_not_found = bool(
                current
                and current.payload.get("status") == "not_found"
                and current.saved_at
                and current.saved_at + timedelta(days=7) > utc_now()
            )
            if not force and current and current.payload.get("plan_fingerprint") == fingerprint and (
                current.payload.get("status") == "success" or reusable_not_found
            ):
                with self._lock:
                    self._state.completed += 1
                continue
            try:
                submission = self.coordinator.submit(
                    account_id=account,
                    resource="course-outline-metadata",
                    variant=course_variant(code),
                    identity_epoch=self.auth_epoch(),
                    force=True,
                    reason=f"metadata_sync:{fingerprint}",
                )
                job = None
                if submission.job_id:
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        job = self.coordinator.get_job(submission.job_id)
                        if job is not None and getattr(job.status, "value", "") in {
                            "completed", "failed", "cancelled"
                        }:
                            break
                        time.sleep(0.1)
                with self._lock:
                    if job is not None and getattr(job.status, "value", "") == "completed":
                        self._state.completed += 1
                    else:
                        self._state.failed += 1
                        self._state.errors.append(code)
            except Exception:
                with self._lock:
                    self._state.failed += 1
                    self._state.errors.append(code)
        with self._lock:
            self._state.running = False
            self._state.current_course = ""
