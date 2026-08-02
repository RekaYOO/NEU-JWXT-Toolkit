"""Background refresh coordination with freshness and identity protection."""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
import uuid
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from .models import (
    AccountScope,
    CacheFetchSkipped,
    CacheJob,
    CacheKey,
    FetchContext,
    JobStatus,
    PayloadType,
    RefreshStatus,
    RefreshSubmission,
    utc_now,
)
from .registry import CacheRegistry, VARIANT_NAME
from .store import CacheStore


IdentityValidator = Callable[[str, int], bool]
EventListener = Callable[[Any], None]
IdentityCommitGuard = Callable[[str, int], AbstractContextManager[Any]]
RemoteGuard = Callable[[], AbstractContextManager[Any]]

PRIORITIES = {
    "foreground_mutation": 0,
    "manual": 10,
    "tracking": 20,
    "login_bootstrap": 30,
    "page_swr": 40,
}


class CacheCoordinator:
    def __init__(
        self,
        store: CacheStore,
        registry: CacheRegistry,
        *,
        identity_validator: IdentityValidator | None = None,
        identity_commit_guard: IdentityCommitGuard | None = None,
        remote_guard: RemoteGuard | None = None,
        worker_count: int = 2,
        autostart: bool = True,
        failure_backoff: timedelta = timedelta(seconds=60),
        job_retention: timedelta = timedelta(minutes=10),
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be positive")
        registry.validate()
        self.store = store
        self.registry = registry
        self.identity_validator = identity_validator or (lambda _account, _epoch: True)
        self.identity_commit_guard = identity_commit_guard or (
            lambda _account, _epoch: nullcontext()
        )
        self.failure_backoff = failure_backoff
        self.job_retention = job_retention
        self.worker_count = worker_count
        self._queue: queue.PriorityQueue[tuple[int, int, str | None]] = (
            queue.PriorityQueue()
        )
        self._jobs: dict[str, CacheJob] = {}
        self._inflight: dict[tuple[int, str, str, str], str] = {}
        self._failures: dict[tuple[int, str, str, str], datetime] = {}
        self._cancelled_jobs: set[str] = set()
        self._deleting_keys: set[tuple[str, str, str]] = set()
        self._listeners: list[EventListener] = []
        self._lock = threading.RLock()
        self._remote_lock = threading.RLock()
        self.remote_guard = remote_guard or (lambda: self._remote_lock)
        self._sequence = 0
        self._accepting = False
        self._threads: list[threading.Thread] = []
        if autostart:
            self.start()

    def start(self) -> None:
        """Start managed workers; safe after a completed bounded shutdown."""
        with self._lock:
            if self._accepting:
                return
            if any(thread.is_alive() for thread in self._threads):
                raise RuntimeError("Cache workers are still stopping")
            self._threads = [
                threading.Thread(
                    target=self._worker,
                    name=f"cache-refresh-{index + 1}",
                    daemon=True,
                )
                for index in range(self.worker_count)
            ]
            self._accepting = True
            threads = tuple(self._threads)
        for thread in threads:
            thread.start()

    def _is_stale(
        self, entry: Any, spec: Any, now: datetime, key: CacheKey
    ) -> bool:
        structurally_stale = (
            entry is None
            or entry.schema_version != spec.schema_version
            or entry.revision_algorithm_version != spec.revision_algorithm_version
            or entry.payload_type != spec.payload_type
            or entry.last_checked_at is None
            or now - entry.last_checked_at >= spec.max_age
        )
        if structurally_stale:
            return True
        for dependency in spec.dependencies:
            dependency_entry = self.store.get(
                CacheKey(key.account_id, dependency, key.variant)
            )
            if (
                dependency_entry is None
                or entry.dependency_revisions.get(dependency)
                != dependency_entry.revision
            ):
                return True
        return False

    @staticmethod
    def _revision(payload_type: PayloadType, canonical: Any, algorithm: int) -> str:
        if payload_type == PayloadType.BLOB:
            if not isinstance(canonical, (bytes, bytearray, memoryview)):
                raise TypeError("BLOB canonical payload must be bytes-like")
            encoded = bytes(canonical)
        else:
            encoded = json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        return f"v{algorithm}:{digest}"

    def submit(
        self,
        *,
        account_id: str,
        resource: str,
        identity_epoch: int,
        variant: str = "default",
        force: bool = False,
        reason: str = "page_swr",
        priority: int | None = None,
    ) -> RefreshSubmission:
        if not account_id.strip():
            raise ValueError("account_id is required")
        if not VARIANT_NAME.fullmatch(variant):
            raise ValueError(f"Invalid cache variant: {variant!r}")
        spec = self.registry.get(resource)
        scoped_account = (
            account_id if spec.account_scope == AccountScope.ACCOUNT else "__global__"
        )
        key = CacheKey(scoped_account, resource, variant)
        now = utc_now()
        entry = self.store.get(key)
        stale = self._is_stale(entry, spec, now, key)
        if not force and not stale:
            return RefreshSubmission(
                RefreshStatus.FRESH, key, None, entry.revision, False
            )
        inflight_key = (identity_epoch, scoped_account, resource, variant)
        with self._lock:
            if not self._accepting:
                raise RuntimeError("Cache coordinator is shutting down")
            if (scoped_account, resource, variant) in self._deleting_keys:
                raise RuntimeError("Cache resource is being deleted")
            existing_id = self._inflight.get(inflight_key)
            if existing_id:
                existing = self._jobs[existing_id]
                return RefreshSubmission(
                    RefreshStatus.RUNNING,
                    key,
                    existing_id,
                    existing.revision or (entry.revision if entry else None),
                    stale,
                )
            if (
                not force
                and (
                    (
                        entry is not None
                        and entry.last_error_kind
                        and entry.last_attempt_at
                        and now - entry.last_attempt_at < self.failure_backoff
                    )
                    or (
                        inflight_key in self._failures
                        and now - self._failures[inflight_key] < self.failure_backoff
                    )
                )
            ):
                return RefreshSubmission(
                    RefreshStatus.THROTTLED,
                    key,
                    None,
                    entry.revision if entry else None,
                    True,
                )
            job_id = uuid.uuid4().hex
            job = CacheJob(
                job_id=job_id,
                key=key,
                identity_epoch=identity_epoch,
                reason=reason,
                status=JobStatus.QUEUED,
                created_at=now,
                updated_at=now,
                previous_revision=entry.revision if entry else None,
            )
            self._jobs[job_id] = job
            self._inflight[inflight_key] = job_id
            self._sequence += 1
            queue_priority = (
                priority if priority is not None else PRIORITIES.get(reason, 50)
            )
            self._queue.put((queue_priority, self._sequence, job_id))
            self._prune_locked(now)
        return RefreshSubmission(
            RefreshStatus.STARTED,
            key,
            job_id,
            entry.revision if entry else None,
            stale,
        )

    def get_job(self, job_id: str) -> CacheJob | None:
        with self._lock:
            self._prune_locked(utc_now())
            job = self._jobs.get(job_id)
            return replace(job) if job else None

    def read(
        self, *, account_id: str, resource: str, variant: str = "default"
    ) -> tuple[Any | None, bool]:
        """Return the current cache entry and whether policy considers it stale."""
        spec = self.registry.get(resource)
        scoped_account = (
            account_id if spec.account_scope == AccountScope.ACCOUNT else "__global__"
        )
        entry = self.store.get(CacheKey(scoped_account, resource, variant))
        return entry, self._is_stale(entry, spec, utc_now(), CacheKey(
            scoped_account, resource, variant
        ))

    def add_event_listener(self, listener: EventListener) -> Callable[[], None]:
        """Subscribe to committed cache events.

        Listeners run after the database transaction and remote-session lock are
        released. A listener exception is isolated from the cache commit.
        """
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            self._listeners.append(listener)

        def remove() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return remove

    def invalidate(
        self, *, account_id: str, resource: str, variant: str = "default"
    ) -> bool:
        spec = self.registry.get(resource)
        scoped_account = (
            account_id if spec.account_scope == AccountScope.ACCOUNT else "__global__"
        )
        return self.store.invalidate(CacheKey(scoped_account, resource, variant))

    def invalidate_after_mutation(
        self,
        *,
        account_id: str,
        resource: str,
        variant: str = "default",
    ) -> tuple[str, ...]:
        spec = self.registry.get(resource)
        invalidated: list[str] = []
        for target in spec.mutation_invalidations:
            if self.invalidate(
                account_id=account_id, resource=target, variant=variant
            ):
                invalidated.append(target)
        return tuple(invalidated)

    def cancel_account(
        self,
        account_id: str,
        *,
        error_kind: str = "identity_changed",
    ) -> int:
        """Cancel queued work for an account; running work is epoch-fenced."""
        cancelled = 0
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if (
                    job.key.account_id == account_id
                    and job.status == JobStatus.QUEUED
                ):
                    self._jobs[job_id] = job.with_updates(
                        status=JobStatus.CANCELLED,
                        error_kind=error_kind,
                    )
                    inflight_key = (
                        job.identity_epoch,
                        job.key.account_id,
                        job.key.resource,
                        job.key.variant,
                    )
                    if self._inflight.get(inflight_key) == job_id:
                        del self._inflight[inflight_key]
                    cancelled += 1
        return cancelled

    def delete_resource(
        self,
        *,
        account_id: str,
        resource: str,
        identity_epoch: int,
        variant: str = "default",
    ) -> tuple[bool, int]:
        """Fence one cache key, cancel its work, and delete it atomically.

        Running remote work cannot be interrupted safely, so it is marked for
        cancellation and checks the marker while holding the same commit guard
        used here.  It may finish its HTTP request, but cannot recreate the
        entry after this method returns.
        """
        spec = self.registry.get(resource)
        scoped_account = (
            account_id if spec.account_scope == AccountScope.ACCOUNT else "__global__"
        )
        key = CacheKey(scoped_account, resource, variant)
        deleting_key = (scoped_account, resource, variant)
        cancelled = 0
        try:
            with self.identity_commit_guard(scoped_account, identity_epoch):
                if not self.identity_validator(scoped_account, identity_epoch):
                    raise RuntimeError("cache identity is no longer active")
                with self._lock:
                    self._deleting_keys.add(deleting_key)
                    for job_id, job in list(self._jobs.items()):
                        if job.key != key or job.status not in {
                            JobStatus.QUEUED, JobStatus.RUNNING
                        }:
                            continue
                        cancelled += 1
                        if job.status == JobStatus.QUEUED:
                            self._jobs[job_id] = job.with_updates(
                                status=JobStatus.CANCELLED,
                                error_kind="resource_deleted",
                            )
                            inflight_key = (
                                job.identity_epoch, scoped_account, resource, variant
                            )
                            if self._inflight.get(inflight_key) == job_id:
                                del self._inflight[inflight_key]
                        else:
                            self._cancelled_jobs.add(job_id)
                deleted = self.store.delete(key)
        finally:
            with self._lock:
                self._deleting_keys.discard(deleting_key)
        return deleted, cancelled

    def _job_cancelled(self, job: CacheJob) -> bool:
        with self._lock:
            return job.job_id in self._cancelled_jobs

    def _set_job(self, job_id: str, **updates: Any) -> CacheJob:
        with self._lock:
            current = self._jobs[job_id]
            updated = current.with_updates(**updates)
            self._jobs[job_id] = updated
            return updated

    def _identity_valid(self, job: CacheJob) -> bool:
        return self.identity_validator(job.key.account_id, job.identity_epoch)

    def _dependency_revisions(self, job: CacheJob) -> dict[str, str]:
        spec = self.registry.get(job.key.resource)
        result: dict[str, str] = {}
        for dependency in spec.dependencies:
            entry = self.store.get(
                CacheKey(job.key.account_id, dependency, job.key.variant)
            )
            if entry:
                result[dependency] = entry.revision
        return result

    def _execute(self, job_id: str) -> None:
        with self._lock:
            current = self._jobs[job_id]
            if current.status != JobStatus.QUEUED:
                return
            job = current.with_updates(status=JobStatus.RUNNING)
            self._jobs[job_id] = job
        if not self._identity_valid(job):
            self._finish(job, status=JobStatus.CANCELLED, error_kind="identity_changed")
            return
        spec = self.registry.get(job.key.resource)
        previous = self.store.get(job.key)
        previous_payload = (
            previous.payload
            if previous
            and previous.schema_version == spec.schema_version
            and previous.revision_algorithm_version
            == spec.revision_algorithm_version
            and previous.payload_type == spec.payload_type
            else None
        )
        dependencies_before = self._dependency_revisions(job)
        try:
            with self.identity_commit_guard(
                job.key.account_id, job.identity_epoch
            ):
                if not self._identity_valid(job) or self._job_cancelled(job):
                    raise _IdentityChanged
                self.store.mark_attempt(job.key)
            context = FetchContext(job.key, job.identity_epoch, job.reason)
            with self.remote_guard():
                if not self._identity_valid(job) or self._job_cancelled(job):
                    raise _IdentityChanged
                fetched = spec.fetch(context)
            if isinstance(fetched, CacheFetchSkipped):
                with self.identity_commit_guard(
                    job.key.account_id, job.identity_epoch
                ):
                    if not self._identity_valid(job) or self._job_cancelled(job):
                        raise _IdentityChanged
                    self.store.mark_skip_success(job.key)
                with self._lock:
                    failure_key = (
                        job.identity_epoch,
                        job.key.account_id,
                        job.key.resource,
                        job.key.variant,
                    )
                    self._failures.pop(failure_key, None)
                self._finish(
                    job,
                    status=JobStatus.COMPLETED,
                    revision=previous.revision if previous else None,
                    changed=False,
                    changes={"skipped": True, "reason": fetched.reason[:64]},
                )
                return
            canonical = spec.canonicalize(fetched)
            revision = self._revision(
                spec.payload_type, canonical, spec.revision_algorithm_version
            )
            changes = dict(spec.diff(previous_payload, canonical))
            # Logout/switch-account must use this same guard. Validation and
            # SQLite commit are then one fenced critical section, so a result
            # cannot recreate cache after account cleanup.
            with self.identity_commit_guard(
                job.key.account_id, job.identity_epoch
            ):
                # Keep the cancellation check and commit under the coordinator
                # lock. delete_resource marks cancellation under this same
                # lock, so deletion and a final commit have a strict order.
                with self._lock:
                    if not self._identity_valid(job) or job.job_id in self._cancelled_jobs:
                        raise _IdentityChanged
                    event = self.store.commit_success(
                        key=job.key,
                        schema_version=spec.schema_version,
                        revision_algorithm_version=spec.revision_algorithm_version,
                        payload_type=spec.payload_type,
                        payload=canonical,
                        revision=revision,
                        dependency_revisions=dependencies_before,
                        changes=changes,
                        reason=job.reason,
                    )
            if dependencies_before != self._dependency_revisions(job):
                self.store.invalidate(job.key)
            if event.changed:
                for dependent in self.registry.dependents_of(job.key.resource):
                    self.store.invalidate(
                        CacheKey(job.key.account_id, dependent, job.key.variant)
                    )
            with self._lock:
                listeners = tuple(self._listeners)
                failure_key = (
                    job.identity_epoch,
                    job.key.account_id,
                    job.key.resource,
                    job.key.variant,
                )
                self._failures.pop(failure_key, None)
            # The cache is already authoritative at this point. Mark the job
            # complete before invoking optional downstream consumers (tracking,
            # dependent refresh scheduling) so a slow listener cannot delay UI.
            self._finish(
                job,
                status=JobStatus.COMPLETED,
                revision=event.revision,
                changed=event.changed,
                changes=event.changes,
            )
            for listener in listeners:
                try:
                    listener(event)
                except Exception:
                    # Notification failures must never roll back committed data.
                    continue
        except _IdentityChanged:
            self._finish(
                job,
                status=JobStatus.CANCELLED,
                error_kind=(
                    "resource_deleted" if self._job_cancelled(job)
                    else "identity_changed"
                ),
            )
        except Exception as exc:
            if self._job_cancelled(job):
                self._finish(
                    job, status=JobStatus.CANCELLED, error_kind="resource_deleted"
                )
                return
            error_kind = type(exc).__name__[:128]
            self.store.mark_failure(job.key, error_kind)
            failure_key = (
                job.identity_epoch,
                job.key.account_id,
                job.key.resource,
                job.key.variant,
            )
            with self._lock:
                self._failures[failure_key] = utc_now()
            self._finish(job, status=JobStatus.FAILED, error_kind=error_kind)

    def _finish(self, original: CacheJob, **updates: Any) -> None:
        self._set_job(original.job_id, **updates)
        key = (
            original.identity_epoch,
            original.key.account_id,
            original.key.resource,
            original.key.variant,
        )
        with self._lock:
            self._cancelled_jobs.discard(original.job_id)
            if self._inflight.get(key) == original.job_id:
                del self._inflight[key]

    def _worker(self) -> None:
        while True:
            _priority, _sequence, job_id = self._queue.get()
            try:
                if job_id is None:
                    return
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job is None or job.status != JobStatus.QUEUED:
                        continue
                self._execute(job_id)
            finally:
                self._queue.task_done()

    def _prune_locked(self, now: datetime) -> None:
        cutoff = now - self.job_retention
        removable = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status
            in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
            and job.updated_at < cutoff
        ]
        for job_id in removable:
            del self._jobs[job_id]

    def shutdown(
        self,
        *,
        wait: bool = True,
        cancel_queued: bool = True,
        timeout: float | None = None,
    ) -> None:
        enqueue_stops = False
        with self._lock:
            if self._accepting:
                self._accepting = False
                enqueue_stops = True
                if cancel_queued:
                    for job_id, job in list(self._jobs.items()):
                        if job.status == JobStatus.QUEUED:
                            self._jobs[job_id] = job.with_updates(
                                status=JobStatus.CANCELLED,
                                error_kind="shutdown",
                            )
                            key = (
                                job.identity_epoch,
                                job.key.account_id,
                                job.key.resource,
                                job.key.variant,
                            )
                            self._inflight.pop(key, None)
        if enqueue_stops:
            for _ in self._threads:
                self._sequence += 1
                self._queue.put((10_000, self._sequence, None))
        if wait:
            deadline = time.monotonic() + timeout if timeout is not None else None
            for thread in self._threads:
                remaining = (
                    max(0.0, deadline - time.monotonic())
                    if deadline is not None
                    else None
                )
                thread.join(remaining)


class _IdentityChanged(Exception):
    pass
