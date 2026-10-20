"""Synthetic end-to-end evidence: raw response -> parser -> cache -> log reader."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from backend.core.academic.report import AcademicReportAPI
from backend.core.cache import CacheCoordinator, CacheKey, CacheRegistry, CacheStore, JobStatus
from backend.core.cache.resources import fetch_academic_report, AcademicReportIdentityError
from backend.core.cache.models import utc_now
from backend.core.log.context import bind_request_context, reset_request_context
from backend.core.log.presentation import present_log_message
from backend.core.log import sync_diagnostics as diagnostics
from backend.core.storage.integration import AcademicReportStorage
from backend.tests.test_cache_core import wait_for


@pytest.fixture
def events(monkeypatch):
    records = []

    class Recorder:
        def log(self, level, template, value):
            records.append(json.loads(value))

    monkeypatch.setattr(diagnostics, "get_logger", lambda *args: Recorder())
    return records


def response(required=160, count=20, *, declared=True):
    root = {"educationalProgramCode": "private-program", "children": [{
        "name": "private-category", "creditsRequired": required,
        "checkCourseVOS": [{"courseName": "private-course", "courseId": f"private-{i}",
                            "studentId": "private-student", "credit": 2,
                            "courseNature": "01"} for i in range(count)],
    }]}
    if declared:
        root["creditsRequired"] = required
    return {"code": "0", "datas": {"queryXyzhbx": {"fanbx": root}}}


def client_for(data):
    return SimpleNamespace(username="private-student", post=lambda *a, **kw:
                           SimpleNamespace(status_code=200, json=lambda: data))


def test_two_day_drop_and_recovery_are_accepted_and_explainable(monkeypatch, tmp_path, events):
    from backend.app import dependencies

    bodies = iter([response(), response(70, 8), response(70, 8), response()])
    client = SimpleNamespace(username="private-student", post=lambda *a, **kw:
                             SimpleNamespace(status_code=200, json=lambda: next(bodies)))
    fetch = lambda context: fetch_academic_report(
        client, lambda report: AcademicReportStorage._report_to_dict(None, report))
    spec = replace(dependencies._cache_registry.get("academic-report"), dependencies=(), fetch=fetch)
    store = CacheStore(tmp_path / "cache.db")
    coordinator = CacheCoordinator(store, CacheRegistry([spec]))
    key = CacheKey(client.username, "academic-report")
    revisions = []
    base = utc_now()
    try:
        for day, credits in enumerate((160, 70, 70, 160)):
            now = base + timedelta(days=day)
            monkeypatch.setattr("backend.core.cache.coordinator.utc_now", lambda: now)
            monkeypatch.setattr("backend.core.cache.store.utc_now", lambda: now)
            monkeypatch.setattr("backend.core.cache.models.utc_now", lambda: now)
            token = bind_request_context(request_id=f"request-{day}")
            try:
                submitted = coordinator.submit(account_id=key.account_id, resource=key.resource,
                                               identity_epoch=1, force=True, reason="manual")
            finally:
                reset_request_context(token)
            job = wait_for(coordinator, submitted.job_id)
            assert job.status == JobStatus.COMPLETED
            entry = store.get(key)
            assert entry.payload["credit_summary"]["total_required"] == credits
            revisions.append(entry.revision)
            # _finish is observable before the wrapper's final event; join workers
            # before inspecting records and exercise restart across days.
            coordinator.shutdown(timeout=2)
            coordinator = CacheCoordinator(store, CacheRegistry([spec]))
        assert revisions[0] == revisions[3]
        assert revisions[1] == revisions[2] != revisions[0]
    finally:
        coordinator.shutdown(timeout=2)

    committed = [event for event in events if event["event"] == "cache_refresh_committed"]
    assert [event["changed"] for event in committed] == [True, True, False, True]
    drop = committed[1]
    assert drop["before"]["credits"]["total_required"] == 160
    assert drop["after"]["credits"]["total_required"] == 70
    assert drop["before"]["course_count"] == 20
    assert drop["after"]["course_count"] == 8
    assert "$.credit_summary.total_required" in drop["field_changes"]["paths"]
    source = next(event for event in events if event["event"] == "academic_report_source"
                  and event["job_id"] == drop["job_id"])
    assert source["declared_required"] == 70
    assert source["required_source"] == "fanbx.creditsRequired"
    assert source["modern_course_rows"] == 8
    assert source["request_id"] == "request-1"
    assert all(event.get("job_id") for event in events)
    assert all(value not in json.dumps(events) for value in (
        "private-student", "private-course", "private-category", "private-program"))
    view = present_log_message("SYNC " + json.dumps(drop))
    assert view.structured and view.details["after"] == drop["after"]


def test_source_distinguishes_fallback_and_legacy_rows(events):
    data = response(70, 2, declared=False)
    root = data["datas"]["queryXyzhbx"]["fanbx"]
    root["children"][0]["data"] = [{"KCM": "private-course", "KCH": "private-code"}]
    report = AcademicReportAPI(client_for(data)).get_report(raise_errors=True)
    assert report.total_required == 70
    source = next(event for event in events if event["event"] == "academic_report_source")
    assert source["required_field_present"] is False
    assert source["required_source"] == "top_category_sum"
    assert source["legacy_course_rows"] == 1 and source["modern_course_rows"] == 2


def test_failure_keeps_original_exception_stage_and_safe_stack(events):
    class PrivateFailure(Exception):
        pass

    client = SimpleNamespace(post=lambda *a, **kw: (_ for _ in ()).throw(
        PrivateFailure("Cookie=private-cookie student=private-student")))
    with pytest.raises(PrivateFailure):
        AcademicReportAPI(client).get_report(raise_errors=True)
    failed = events[-1]
    assert failed["stage"] == "request"
    assert failed["error_type"] == "PrivateFailure" and failed["trace"]
    assert "private-cookie" not in json.dumps(events)


def test_wrong_account_rejected_without_retry(monkeypatch):
    calls = []
    def report(*args, **kwargs):
        calls.append(kwargs)
        return object()
    monkeypatch.setattr(AcademicReportAPI, "get_report", report)
    with pytest.raises(AcademicReportIdentityError):
        fetch_academic_report(SimpleNamespace(username="a"), lambda r: {"student_id": "b"})
    assert len(calls) == 1


def test_diagnostic_handler_failure_does_not_fail_refresh(monkeypatch, tmp_path):
    from backend.tests.test_cache_core import spec
    monkeypatch.setattr(diagnostics, "get_logger", lambda *args: (_ for _ in ()).throw(OSError()))
    coordinator = CacheCoordinator(CacheStore(tmp_path / "cache.db"),
                                   CacheRegistry([spec("scores", lambda context: {"ok": True})]))
    try:
        submitted = coordinator.submit(account_id="a", resource="scores", identity_epoch=1)
        assert wait_for(coordinator, submitted.job_id).status == JobStatus.COMPLETED
    finally:
        coordinator.shutdown(timeout=2)


def test_cosmetic_timetable_change_and_numeric_change_are_visible():
    before = {"courses": [{"meeting_id": "private-id", "color": "red", "weekday": 1}]}
    after = deepcopy(before)
    after["courses"][0].update(color="blue", weekday=2)
    evidence = diagnostics.cache_evidence("personal-timetable", before, after)
    assert evidence["field_changes"]["paths"] == {
        "$.courses[].color": 1, "$.courses[].weekday": 1}
    assert "private-id" not in json.dumps(evidence)


def test_context_does_not_leak_between_jobs(events):
    with diagnostics.sync_context(job_id="first"):
        diagnostics.emit("cache_refresh_started")
    diagnostics.emit("academic_report_request")
    assert events[0]["job_id"] == "first" and "job_id" not in events[1]


@pytest.mark.parametrize("stage", ["decode_json", "response_shape", "parse_report"])
def test_decode_shape_and_parse_failures_are_distinct(stage, events):
    body = response()
    def decode():
        if stage == "decode_json":
            raise ValueError("private-body")
        return body
    if stage == "response_shape":
        body["datas"]["queryXyzhbx"] = {}
    if stage == "parse_report":
        body["datas"]["queryXyzhbx"]["fanbx"]["creditsRequired"] = "private-bad-number"
    client = SimpleNamespace(post=lambda *a, **kw: SimpleNamespace(status_code=200, json=decode))
    assert AcademicReportAPI(client).get_report() is None
    assert events[-1]["event"] == "academic_report_failed"
    assert events[-1]["stage"] == stage
    assert "private-body" not in json.dumps(events)
    assert "private-bad-number" not in json.dumps(events)


def test_failed_refresh_preserves_cache_and_links_error_to_job(tmp_path, events):
    from backend.tests.test_cache_core import spec
    fail = False
    def fetch(context):
        if fail:
            raise ValueError("private-response")
        return {"value": 160}
    store = CacheStore(tmp_path / "cache.db")
    coordinator = CacheCoordinator(store, CacheRegistry([spec("academic-report", fetch)]))
    def refresh():
        submission = coordinator.submit(account_id="private-student", resource="academic-report",
                                        identity_epoch=1, force=True)
        return wait_for(coordinator, submission.job_id)
    try:
        assert refresh().status == JobStatus.COMPLETED
        key = CacheKey("private-student", "academic-report")
        previous = store.get(key)
        fail = True
        job = refresh()
        assert job.status == JobStatus.FAILED and job.error_kind == "ValueError"
        current = store.get(key)
        assert current.payload == previous.payload and current.revision == previous.revision
        assert current.saved_at == previous.saved_at
        assert current.last_checked_at == previous.last_checked_at
    finally:
        coordinator.shutdown(timeout=2)
    event = next(event for event in events if event["event"] == "cache_refresh_failed")
    assert event["job_id"] == event["error_id"] == job.job_id
    assert event["stage"] == "fetch" and event["trace"]
    assert "private-response" not in json.dumps(events)


def test_timetable_remote_input_and_parsed_counts_are_correlated(events):
    from backend.core.timetable.api import TimetableAPI
    body = {"code": "0", "datas": {"arrangedList": [], "notArrangeList": [], "practiceList": []}}
    with diagnostics.sync_context(job_id="timetable-job", resource="personal-timetable"):
        TimetableAPI(client_for(body)).get_schedule(
            mode="personal", term_code="2026-2027-1", campus_code="all")
    request, remote, source = events
    assert request["event"] == "timetable_remote_request"
    assert request["week_filtered"] is False and request["campus_filtered"] is False
    assert remote["response_fingerprint"] == diagnostics.fingerprint(body)
    assert source["counts"] == {"arrangedList": 0, "notArrangeList": 0, "practiceList": 0}
    assert all(event["job_id"] == "timetable-job" for event in events)


def test_time_only_change_has_same_content_fingerprint():
    before = response()["datas"]["queryXyzhbx"]
    after = {**before, "calculatedTime": "different"}
    old, new = diagnostics.report_source(before), diagnostics.report_source(after)
    assert old["response_fingerprint"] != new["response_fingerprint"]
    assert old["content_fingerprint"] == new["content_fingerprint"]


def test_diagnostic_schema_errors_are_nonfatal():
    evidence = diagnostics.cache_evidence("academic-report", {}, {"categories": [None]})
    assert evidence["after"]["diagnostic_error"] == "AttributeError"
    assert diagnostics.report_source({"fanbx": []})["category_count"] == 0


def test_sync_log_file_search_and_download_keep_diagnostic_details(monkeypatch, tmp_path):
    import logging
    from datetime import datetime
    from backend.core.log.logger import DailyRotatingFileHandler, LogCategory, LogConfig
    from backend.core.log.manager import LogManager

    config = LogConfig(log_dir=str(tmp_path), console_output=False)
    handler = DailyRotatingFileHandler(config, LogCategory.SYNC)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger = logging.Logger("neu.sync.refresh")
    logger.addHandler(handler)
    monkeypatch.setattr(diagnostics, "get_logger", lambda *args: logger)
    try:
        with diagnostics.sync_context(job_id="searchable-job", resource="academic-report"):
            diagnostics.emit("cache_refresh_committed", changed=True,
                             before={"credits": {"total_required": 160}},
                             after={"credits": {"total_required": 70}})
    finally:
        handler.close()
    manager = LogManager(config)
    date = datetime.now().strftime("%Y-%m-%d")
    entries = manager.read_log(LogCategory.SYNC, date, search="searchable-job")
    assert len(entries) == 1 and entries[0].event_title == "缓存刷新提交"
    assert entries[0].details["after"]["credits"]["total_required"] == 70
    assert manager.search_logs("searchable-job", LogCategory.SYNC)
    assert b"searchable-job" in manager.download_log(LogCategory.SYNC, date)
