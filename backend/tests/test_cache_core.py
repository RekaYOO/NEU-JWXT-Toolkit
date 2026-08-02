from __future__ import annotations

import os
import threading
import time
from datetime import timedelta

import pytest

from backend.core.cache import (
    AccountScope,
    CacheCoordinator,
    CacheFetchSkipped,
    CacheKey,
    CacheRegistry,
    CacheResourceSpec,
    CacheStore,
    JobStatus,
    PayloadType,
    RefreshStatus,
)


def spec(resource, fetch, *, dependencies=(), max_age=timedelta(minutes=5)):
    return CacheResourceSpec(
        resource=resource,
        schema_version=1,
        revision_algorithm_version=1,
        account_scope=AccountScope.ACCOUNT,
        payload_type=PayloadType.JSON,
        max_age=max_age,
        offline_readable=True,
        sensitivity="personal-academic",
        fetch=fetch,
        dependencies=dependencies,
    )


def wait_for(coordinator, job_id, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = coordinator.get_job(job_id)
        if job and job.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
            return job
        time.sleep(0.01)
    raise AssertionError("cache job did not finish")


def test_coordinator_can_restart_after_managed_shutdown(tmp_path):
    registry = CacheRegistry((spec("scores", lambda _context: {"value": 1}),))
    coordinator = CacheCoordinator(
        CacheStore(tmp_path / "cache.db"),
        registry,
        autostart=False,
    )
    coordinator.start()
    first = coordinator.submit(
        account_id="a", resource="scores", identity_epoch=1, force=True,
    )
    assert wait_for(coordinator, first.job_id).status == JobStatus.COMPLETED
    coordinator.shutdown(timeout=2)

    coordinator.start()
    second = coordinator.submit(
        account_id="a", resource="scores", identity_epoch=1, force=True,
    )
    assert wait_for(coordinator, second.job_id).status == JobStatus.COMPLETED
    coordinator.shutdown(timeout=2)


def test_store_round_trip_events_and_stable_saved_at(tmp_path):
    store = CacheStore(tmp_path / "cache.db")
    key = CacheKey("account-a", "scores")
    first = store.commit_success(
        key=key,
        schema_version=1,
        revision_algorithm_version=1,
        payload_type=PayloadType.JSON,
        payload={"score": 90},
        revision="v1:first",
        dependency_revisions={},
        changes={"added": 1},
        reason="manual",
    )
    original = store.get(key)
    second = store.commit_success(
        key=key,
        schema_version=1,
        revision_algorithm_version=1,
        payload_type=PayloadType.JSON,
        payload={"score": 90},
        revision="v1:first",
        dependency_revisions={},
        changes={},
        reason="page_swr",
    )
    current = store.get(key)

    assert original is not None and current is not None
    assert current.payload == {"score": 90}
    assert current.saved_at == original.saved_at
    assert current.last_checked_at >= original.last_checked_at
    assert first.changed is True
    assert second.changed is False
    assert [event.cursor for event in store.events_after("account-a")] == [1, 2]
    assert store.events_after("other") == []
    if os.name != "nt":
        assert (store.path.stat().st_mode & 0o777) == 0o600


def test_store_blob_delete_and_account_isolation(tmp_path):
    store = CacheStore(tmp_path / "cache.db")
    for account in ("a", "b"):
        store.commit_success(
            key=CacheKey(account, "avatar"),
            schema_version=1,
            revision_algorithm_version=1,
            payload_type=PayloadType.BLOB,
            payload=b"image",
            revision="v1:image",
            dependency_revisions={},
            changes={},
            reason="manual",
        )
    assert store.get(CacheKey("a", "avatar")).payload == b"image"
    assert store.delete_account("a") == 1
    assert store.get(CacheKey("a", "avatar")) is None
    assert store.get(CacheKey("b", "avatar")) is not None


def test_registry_rejects_missing_dependency_and_cycles():
    registry = CacheRegistry()
    registry.register(spec("scores", lambda _: []))
    registry.register(
        spec("academic-report", lambda _: {}, dependencies=("missing",))
    )
    with pytest.raises(ValueError, match="unknown resources"):
        registry.validate()

    cyclic = CacheRegistry()
    cyclic.register(spec("one", lambda _: {}, dependencies=("two",)))
    cyclic.register(spec("two", lambda _: {}, dependencies=("one",)))
    with pytest.raises(ValueError, match="cycle"):
        cyclic.validate()


def test_coordinator_singleflight_freshness_and_event(tmp_path):
    gate = threading.Event()
    calls = []

    def fetch(context):
        calls.append(context)
        gate.wait(1)
        return [{"course": "A", "score": 90}]

    registry = CacheRegistry([spec("scores", fetch)])
    coordinator = CacheCoordinator(CacheStore(tmp_path / "cache.db"), registry)
    try:
        first = coordinator.submit(
            account_id="a", resource="scores", identity_epoch=1
        )
        duplicate = coordinator.submit(
            account_id="a", resource="scores", identity_epoch=1
        )
        assert first.status == RefreshStatus.STARTED
        assert duplicate.status == RefreshStatus.RUNNING
        assert duplicate.job_id == first.job_id
        gate.set()
        job = wait_for(coordinator, first.job_id)
        assert job.status == JobStatus.COMPLETED
        assert job.changed is True
        assert len(calls) == 1

        fresh = coordinator.submit(
            account_id="a", resource="scores", identity_epoch=1
        )
        assert fresh.status == RefreshStatus.FRESH
        assert fresh.job_id is None
    finally:
        coordinator.shutdown()


def test_schema_or_revision_algorithm_upgrade_forces_refresh(tmp_path):
    store = CacheStore(tmp_path / "cache.db")
    store.commit_success(
        key=CacheKey("a", "scores"),
        schema_version=1,
        revision_algorithm_version=1,
        payload_type=PayloadType.JSON,
        payload={"legacy": True},
        revision="v1:legacy",
        dependency_revisions={},
        changes={},
        reason="migration",
    )
    upgraded = CacheResourceSpec(
        **{
            **spec("scores", lambda _: {"current": True}).__dict__,
            "schema_version": 2,
        }
    )
    coordinator = CacheCoordinator(store, CacheRegistry([upgraded]))
    try:
        submission = coordinator.submit(
            account_id="a", resource="scores", identity_epoch=1
        )
        assert submission.status == RefreshStatus.STARTED
        assert wait_for(coordinator, submission.job_id).status == JobStatus.COMPLETED
        assert store.get(CacheKey("a", "scores")).schema_version == 2
        assert store.get(CacheKey("a", "scores")).payload == {"current": True}
    finally:
        coordinator.shutdown()


def test_changed_dependency_is_invalidated(tmp_path):
    values = iter(([{"score": 90}], [{"score": 91}]))
    registry = CacheRegistry(
        [
            spec("scores", lambda _: next(values)),
            spec("academic-report", lambda _: {"done": True}, dependencies=("scores",)),
        ]
    )
    store = CacheStore(tmp_path / "cache.db")
    coordinator = CacheCoordinator(store, registry)
    try:
        report_job = coordinator.submit(
            account_id="a",
            resource="academic-report",
            identity_epoch=1,
            force=True,
        )
        wait_for(coordinator, report_job.job_id)
        before = store.get(CacheKey("a", "academic-report"))
        assert before.last_checked_at is not None

        score_job = coordinator.submit(
            account_id="a", resource="scores", identity_epoch=1, force=True
        )
        wait_for(coordinator, score_job.job_id)
        after = store.get(CacheKey("a", "academic-report"))
        assert after.last_checked_at is None
    finally:
        coordinator.shutdown()


def test_failed_refresh_is_throttled_and_preserves_cache(tmp_path):
    registry = CacheRegistry([spec("scores", lambda _: (_ for _ in ()).throw(TimeoutError()))])
    store = CacheStore(tmp_path / "cache.db")
    store.commit_success(
        key=CacheKey("a", "scores"),
        schema_version=1,
        revision_algorithm_version=1,
        payload_type=PayloadType.JSON,
        payload=[{"score": 90}],
        revision="old",
        dependency_revisions={},
        changes={},
        reason="migration",
    )
    store.invalidate(CacheKey("a", "scores"))
    coordinator = CacheCoordinator(store, registry)
    try:
        submission = coordinator.submit(
            account_id="a", resource="scores", identity_epoch=1
        )
        assert wait_for(coordinator, submission.job_id).status == JobStatus.FAILED
        assert store.get(CacheKey("a", "scores")).payload == [{"score": 90}]
        retry = coordinator.submit(
            account_id="a", resource="scores", identity_epoch=1
        )
        assert retry.status == RefreshStatus.THROTTLED
    finally:
        coordinator.shutdown()


def test_first_failed_refresh_is_also_throttled(tmp_path):
    registry = CacheRegistry(
        [spec("scores", lambda _: (_ for _ in ()).throw(ConnectionError()))]
    )
    coordinator = CacheCoordinator(CacheStore(tmp_path / "cache.db"), registry)
    try:
        submission = coordinator.submit(
            account_id="a", resource="scores", identity_epoch=1
        )
        assert wait_for(coordinator, submission.job_id).status == JobStatus.FAILED
        retry = coordinator.submit(
            account_id="a", resource="scores", identity_epoch=1
        )
        assert retry.status == RefreshStatus.THROTTLED
    finally:
        coordinator.shutdown()


def test_identity_epoch_prevents_stale_commit(tmp_path):
    epoch = {"value": 1}
    started = threading.Event()
    release = threading.Event()

    def fetch(_):
        started.set()
        release.wait(1)
        return {"secret": "old-account-result"}

    registry = CacheRegistry([spec("scores", fetch)])
    store = CacheStore(tmp_path / "cache.db")
    coordinator = CacheCoordinator(
        store,
        registry,
        identity_validator=lambda account, expected: (
            account == "a" and epoch["value"] == expected
        ),
    )
    try:
        submission = coordinator.submit(
            account_id="a", resource="scores", identity_epoch=1
        )
        assert started.wait(1)
        epoch["value"] = 2
        release.set()
        job = wait_for(coordinator, submission.job_id)
        assert job.status == JobStatus.CANCELLED
        assert store.get(CacheKey("a", "scores")) is None
    finally:
        coordinator.shutdown()


def test_commit_guard_fences_logout_cleanup(tmp_path):
    auth_lock = threading.RLock()
    epoch = {"value": 1}
    fetched = threading.Event()
    allow_fetch_return = threading.Event()

    def fetch(_):
        fetched.set()
        allow_fetch_return.wait(1)
        return {"score": 90}

    store = CacheStore(tmp_path / "cache.db")
    coordinator = CacheCoordinator(
        store,
        CacheRegistry([spec("scores", fetch)]),
        identity_validator=lambda _account, expected: epoch["value"] == expected,
        identity_commit_guard=lambda _account, _expected: auth_lock,
    )
    try:
        submission = coordinator.submit(
            account_id="a", resource="scores", identity_epoch=1
        )
        assert fetched.wait(1)
        # Model logout: it owns the same state lock while advancing the epoch
        # and deleting account data. The worker cannot validate+commit across it.
        with auth_lock:
            epoch["value"] = 2
            store.delete_account("a")
            allow_fetch_return.set()
        job = wait_for(coordinator, submission.job_id)
        assert job.status == JobStatus.CANCELLED
        assert store.get(CacheKey("a", "scores")) is None
    finally:
        coordinator.shutdown()


def test_remote_fetches_are_serialized_with_two_workers(tmp_path):
    active = 0
    maximum = 0
    lock = threading.Lock()

    def fetch(_):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.04)
        with lock:
            active -= 1
        return {}

    registry = CacheRegistry([spec("scores", fetch)])
    coordinator = CacheCoordinator(
        CacheStore(tmp_path / "cache.db"), registry, worker_count=2
    )
    try:
        one = coordinator.submit(account_id="a", resource="scores", identity_epoch=1)
        two = coordinator.submit(account_id="b", resource="scores", identity_epoch=1)
        wait_for(coordinator, one.job_id)
        wait_for(coordinator, two.job_id)
        assert maximum == 1
    finally:
        coordinator.shutdown()


def test_listener_failure_isolated_and_mutation_invalidation(tmp_path):
    registry = CacheRegistry(
        [
            CacheResourceSpec(
                **{
                    **spec("scores", lambda _: [{"score": 90}]).__dict__,
                    "mutation_invalidations": ("academic-report",),
                }
            ),
            spec("academic-report", lambda _: {"done": True}),
        ]
    )
    store = CacheStore(tmp_path / "cache.db")
    coordinator = CacheCoordinator(store, registry)
    observed = []
    coordinator.add_event_listener(lambda event: observed.append(event))
    coordinator.add_event_listener(
        lambda _event: (_ for _ in ()).throw(RuntimeError("subscriber failed"))
    )
    try:
        report = coordinator.submit(
            account_id="a",
            resource="academic-report",
            identity_epoch=1,
            force=True,
        )
        wait_for(coordinator, report.job_id)
        assert coordinator.invalidate_after_mutation(
            account_id="a", resource="scores"
        ) == ("academic-report",)

        scores = coordinator.submit(
            account_id="a", resource="scores", identity_epoch=1, force=True
        )
        assert wait_for(coordinator, scores.job_id).status == JobStatus.COMPLETED
        assert observed[-1].key.resource == "scores"
    finally:
        coordinator.shutdown()


def test_shutdown_cancelled_queue_item_is_not_executed(tmp_path):
    first_started = threading.Event()
    release_first = threading.Event()
    calls = []

    def fetch(context):
        calls.append(context.key.account_id)
        if context.key.account_id == "a":
            first_started.set()
            release_first.wait(1)
        return {}

    coordinator = CacheCoordinator(
        CacheStore(tmp_path / "cache.db"),
        CacheRegistry([spec("scores", fetch)]),
        worker_count=1,
    )
    first = coordinator.submit(account_id="a", resource="scores", identity_epoch=1)
    assert first_started.wait(1)
    second = coordinator.submit(account_id="b", resource="scores", identity_epoch=1)
    coordinator.shutdown(wait=False, cancel_queued=True)
    release_first.set()
    coordinator.shutdown(wait=True, timeout=1)

    assert wait_for(coordinator, first.job_id).status == JobStatus.COMPLETED
    assert coordinator.get_job(second.job_id).status == JobStatus.CANCELLED
    assert calls == ["a"]


def test_logout_cancels_queued_account_work(tmp_path):
    first_started = threading.Event()
    release_first = threading.Event()
    calls = []

    def fetch(context):
        calls.append(context.key.account_id)
        if context.key.account_id == "busy":
            first_started.set()
            release_first.wait(1)
        return {}

    coordinator = CacheCoordinator(
        CacheStore(tmp_path / "cache.db"),
        CacheRegistry([spec("scores", fetch)]),
        worker_count=1,
    )
    try:
        busy = coordinator.submit(
            account_id="busy", resource="scores", identity_epoch=1
        )
        assert first_started.wait(1)
        queued = coordinator.submit(
            account_id="logout-account",
            resource="scores",
            identity_epoch=1,
        )

        assert coordinator.cancel_account("logout-account") == 1
        release_first.set()
        assert wait_for(coordinator, busy.job_id).status == JobStatus.COMPLETED
        assert coordinator.get_job(queued.job_id).status == JobStatus.CANCELLED
        assert calls == ["busy"]
    finally:
        release_first.set()
        coordinator.shutdown(timeout=1)


def test_skipped_fetch_completes_without_overwriting_existing_cache(tmp_path):
    store = CacheStore(tmp_path / "cache.db")
    key = CacheKey("a", "score-details", "course-one")
    store.commit_success(
        key=key,
        schema_version=1,
        revision_algorithm_version=1,
        payload_type=PayloadType.JSON,
        payload={"item_scores": [{"name": "平时成绩", "value": "90"}]},
        revision="v1:existing",
        dependency_revisions={},
        changes={"added": 1},
        reason="manual",
    )
    previous = store.get(key)
    assert previous is not None
    store.mark_failure(key, "OldFailure")
    assert store.get(key).last_error_kind == "OldFailure"
    coordinator = CacheCoordinator(
        store,
        CacheRegistry([
            spec(
                "score-details",
                lambda _context: CacheFetchSkipped("no_detail_data"),
            )
        ]),
    )
    try:
        submission = coordinator.submit(
            account_id="a",
            resource="score-details",
            variant="course-one",
            identity_epoch=1,
            force=True,
        )
        job = wait_for(coordinator, submission.job_id)

        assert job.status == JobStatus.COMPLETED
        assert job.changed is False
        assert job.revision == "v1:existing"
        assert job.changes == {"skipped": True, "reason": "no_detail_data"}
        current = store.get(key)
        assert current is not None
        assert current.revision == "v1:existing"
        assert current.payload == {
            "item_scores": [{"name": "平时成绩", "value": "90"}]
        }
        assert current.saved_at == previous.saved_at
        assert current.last_error_kind is None
        assert current.last_checked_at is not None
        assert current.last_checked_at >= previous.last_checked_at
    finally:
        coordinator.shutdown(timeout=1)
