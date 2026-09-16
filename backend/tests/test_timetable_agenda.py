from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.routers import timetable
from backend.app.schemas.timetable_agenda import AgendaDocument
from backend.core.storage.timetable_agenda import (
    AgendaConflict, AgendaSemesterEnded, read_agenda, read_agenda_state,
    reconcile_agenda_semesters, save_agenda, semester_calendar,
)


@pytest.fixture(autouse=True)
def local_coordinator(monkeypatch):
    spec = SimpleNamespace(schema_version=1, revision_algorithm_version=1, payload_type="json")
    coordinator = SimpleNamespace(
        registry=SimpleNamespace(get=lambda _: spec),
        store=SimpleNamespace(list_entries=Mock(return_value=[])),
        read=Mock(return_value=(None, True)),
        submit=Mock(side_effect=AssertionError("Agenda must never start remote work")),
    )
    monkeypatch.setattr(timetable, "get_cache_coordinator", lambda: coordinator)
    return coordinator


def event(**changes):
    return {"id": "event-1", "date": "2026-09-17", "title": "日程", "start_time": "08:00", "end_time": "09:00", **changes}


@pytest.mark.parametrize("changes", [{"title": " "}, {"end_time": "07:00"}, {"start_time": "25:00"}, {"id": "../escape"}, {"date": "2026-02-30"}, {"title": "x" * 121}])
def test_invalid_events(changes):
    with pytest.raises(ValidationError):
        AgendaDocument(events=[event(**changes)])


def test_duplicates_and_copy_dates():
    with pytest.raises(ValidationError):
        AgendaDocument(events=[event(), event()])
    with pytest.raises(ValidationError):
        AgendaDocument(moves=[{"source": "2026-09-20", "target": "2026-09-20"}])
    with pytest.raises(ValidationError):
        AgendaDocument(moves=[{"source": "2026-09-20", "target": "2026-09-17"}, {"source": "2026-09-21", "target": "2026-09-17"}])
    assert len(AgendaDocument(moves=[{"source": "2026-09-20", "target": "2026-09-17"}, {"source": "2026-09-20", "target": "2026-09-18"}]).moves) == 2


def test_persistence_account_term_isolation_and_revision(tmp_path):
    payload = AgendaDocument(events=[event()]).model_dump(mode="json")
    saved = save_agenda(tmp_path, "account-a", "term-a", payload)
    assert saved["revision"] == 1
    assert read_agenda(tmp_path, "account-a", "term-a") == saved
    assert read_agenda(tmp_path, "account-b", "term-a")["events"] == []
    assert read_agenda(tmp_path, "account-a", "term-b")["events"] == []
    with pytest.raises(AgendaConflict):
        save_agenda(tmp_path, "account-a", "term-a", payload)
    assert read_agenda(tmp_path, "account-a", "term-a") == saved
    assert not list(tmp_path.rglob("*.tmp"))
    assert "account-a" not in str(next(tmp_path.rglob("*.json")))


def test_api_reads_writes_and_conflicts_without_remote_login(tmp_path):
    app = FastAPI()
    app.include_router(timetable.router, prefix="/api")
    auth = SimpleNamespace(username="account-a")
    app.dependency_overrides[timetable.require_cached_auth_identity] = lambda: auth
    app.dependency_overrides[timetable.get_storage] = lambda: SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    with TestClient(app) as client:
        url = "/api/timetable/agenda?term_code=2026-2027-1"
        assert client.get(url).json()["revision"] == 0
        response = client.put(url, json={"events": [event()]})
        assert response.status_code == 200
        assert client.get(url).json() == response.json()
        assert client.put(url, json={"events": [event()]}).status_code == 409
        assert client.put(url, json={"events": [event(end_time="07:00")]}).status_code == 422
        today = datetime.now(timezone(timedelta(hours=8))).date()
        payload = {key: response.json()[key] for key in ("revision", "events", "moves")}
        payload["moves"] = [{"source": today.isoformat(), "target": (today - timedelta(days=1)).isoformat()}]
        assert client.put(url, json=payload).status_code == 422
        payload["moves"][0]["source"] = (today + timedelta(days=1)).isoformat()
        saved = client.put(url, json=payload)
        assert saved.status_code == 200
        auth.username = "account-b"
        assert client.get(url).json()["events"] == []
        assert client.get("/api/timetable/agenda?term_code=../escape").status_code == 422


def calendar(start="2026-09-06", count=3):
    from datetime import date
    first = date.fromisoformat(start)
    return [{"number": i + 1, "start_date": (first + timedelta(days=i * 7)).isoformat(),
             "end_date": (first + timedelta(days=i * 7 + 6)).isoformat()} for i in range(count)]


@pytest.mark.parametrize("weeks", [[], None, calendar()[1:], calendar()[::2],
                                  calendar("2026-09-07"), [{**calendar()[0], "end_date": "2026-10-01"}]])
def test_partial_or_invalid_calendar_cannot_expire_agenda(weeks):
    assert semester_calendar(weeks) is None


def test_expiry_and_retirement_are_separate_and_preserve_revision(tmp_path):
    from datetime import date
    old = semester_calendar(calendar("2026-02-01", 2))
    upcoming = semester_calendar(calendar())
    saved = save_agenda(tmp_path, "a", "old", AgendaDocument(events=[event()]).model_dump(mode="json"),
                        calendar=old, today=date(2026, 2, 1))
    for today, current in [(date(2026, 2, 14), "old"), (date(2026, 8, 30), "next")]:
        reconcile_agenda_semesters(tmp_path, "a", {"old": old, "next": upcoming}, current, today=today)
        assert read_agenda(tmp_path, "a", "old") == saved
    assert read_agenda_state(tmp_path, "a", "old", today=date(2026, 8, 30))["semester_ended"]
    # Previewing a future semester, a missing index, or a cache purge is not proof.
    reconcile_agenda_semesters(tmp_path, "a", {}, "", today=date(2026, 9, 16))
    assert read_agenda(tmp_path, "a", "old") == saved
    reconcile_agenda_semesters(tmp_path, "a", {"next": upcoming}, "next", today=date(2026, 9, 16))
    retired = read_agenda_state(tmp_path, "a", "old", today=date(2026, 9, 16))
    assert retired == {"revision": 2, "events": [], "moves": [], "semester_ended": True, "semester_end": old["end"]}
    with pytest.raises(AgendaConflict):
        save_agenda(tmp_path, "a", "old", saved)
    with pytest.raises(AgendaSemesterEnded):
        save_agenda(tmp_path, "a", "old", {**saved, "revision": 2})
    reconcile_agenda_semesters(tmp_path, "a", {"next": upcoming}, "next", today=date(2026, 9, 16))
    assert read_agenda(tmp_path, "a", "old")["revision"] == 2


def test_retirement_is_account_isolated_and_does_not_touch_unrecognized_documents(tmp_path):
    from datetime import date
    payload = AgendaDocument(events=[event()]).model_dump(mode="json")
    span = semester_calendar(calendar("2026-02-01", 2))
    for account in ("a", "b"):
        save_agenda(tmp_path, account, "old", payload, calendar=span, today=date(2026, 2, 1))
    save_agenda(tmp_path, "a", "unknown", payload)
    unrelated = tmp_path / "cache.db"
    unrelated.write_bytes(b"official cache")
    reconcile_agenda_semesters(tmp_path, "a", {"next": semester_calendar(calendar())}, "next", today=date(2026, 9, 16))
    assert read_agenda(tmp_path, "b", "old")["events"]
    assert read_agenda(tmp_path, "a", "unknown")["events"]
    assert unrelated.read_bytes() == b"official cache"


def test_calendar_extension_never_shortens_retention(tmp_path):
    from datetime import date
    span = semester_calendar(calendar(count=3))
    save_agenda(tmp_path, "a", "term", AgendaDocument(events=[event()]).model_dump(mode="json"),
                calendar=span, today=date(2026, 9, 6))
    reconcile_agenda_semesters(tmp_path, "a", {"term": semester_calendar(calendar(count=1))}, "term", today=date(2026, 9, 16))
    assert not read_agenda_state(tmp_path, "a", "term", today=date(2026, 9, 16))["semester_ended"]


def test_local_lifecycle_only_uses_fresh_compatible_account_caches(tmp_path, local_coordinator):
    entry = SimpleNamespace(schema_version=1, revision_algorithm_version=1, payload_type="json",
                            payload={"term_code": "2026-2027-1", "weeks": calendar()}, key=SimpleNamespace(variant="term:2026-2027-1"))
    index = SimpleNamespace(schema_version=1, revision_algorithm_version=1, payload_type="json",
                            payload={"current": "2026-2027-1", "terms": [{"code": "2026-2027-1", "current": True}]})
    local_coordinator.store.list_entries.return_value = [entry]
    local_coordinator.read.side_effect = lambda **kw: (index if kw["resource"] == "timetable-index" else entry, False)
    storage = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    assert timetable._agenda_calendars(storage, "a") == {"2026-2027-1": semester_calendar(calendar())}
    local_coordinator.store.list_entries.assert_called_once_with(account_id="a", resource="personal-timetable")
    local_coordinator.read.side_effect = lambda **kw: (entry, True)
    assert timetable._agenda_calendars(storage, "a") == {}
    local_coordinator.submit.assert_not_called()


def test_corrupt_document_is_not_overwritten_by_lifecycle_or_save(tmp_path):
    from datetime import date
    from backend.core.storage.timetable_agenda import _path
    path = _path(tmp_path, "a", "term")
    path.parent.mkdir(parents=True)
    path.write_text("broken", encoding="utf-8")
    reconcile_agenda_semesters(tmp_path, "a", {"term": semester_calendar(calendar())}, "term", today=date(2026, 9, 16))
    assert path.read_text(encoding="utf-8") == "broken"
    with pytest.raises(ValueError):
        save_agenda(tmp_path, "a", "term", AgendaDocument().model_dump(mode="json"))


def test_api_cannot_resurrect_a_retired_agenda(tmp_path):
    from datetime import date
    span = semester_calendar(calendar("2026-02-01", 2))
    saved = save_agenda(tmp_path, "a", "old", AgendaDocument(events=[event()]).model_dump(mode="json"), calendar=span, today=date(2026, 2, 1))
    reconcile_agenda_semesters(tmp_path, "a", {"new": semester_calendar(calendar())}, "new", today=date(2026, 9, 16))
    app = FastAPI()
    app.include_router(timetable.router, prefix="/api")
    app.dependency_overrides[timetable.require_cached_auth_identity] = lambda: SimpleNamespace(username="a")
    app.dependency_overrides[timetable.get_storage] = lambda: SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    with TestClient(app) as client:
        url = "/api/timetable/agenda?term_code=old"
        assert client.get(url).json()["semester_ended"]
        assert client.put(url, json=saved).status_code == 409
        saved["revision"] += 1
        assert client.put(url, json=saved).status_code == 410
        assert client.get(url).json()["events"] == []


def test_cache_database_failure_does_not_block_agenda_or_trigger_cleanup(tmp_path, local_coordinator):
    import sqlite3
    saved = save_agenda(tmp_path, "a", "term", AgendaDocument(events=[event()]).model_dump(mode="json"))
    local_coordinator.store.list_entries.side_effect = sqlite3.OperationalError("cache unavailable")
    storage = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    assert timetable._agenda_calendars(storage, "a") == {}
    assert read_agenda(tmp_path, "a", "term") == saved
    assert timetable.get_agenda("term", SimpleNamespace(username="a"), storage)["events"]
    local_coordinator.submit.assert_not_called()


def test_agenda_routes_require_identity():
    routes = [route for route in timetable.router.routes if getattr(route, "path", "") == "/timetable/agenda"]
    assert len(routes) == 2
    assert all(any(dependency.call is timetable.require_cached_auth_identity for dependency in route.dependant.dependencies) for route in routes)


def test_api_edits_and_deletes_only_the_target_event(tmp_path):
    app = FastAPI()
    app.include_router(timetable.router, prefix="/api")
    app.dependency_overrides[timetable.require_cached_auth_identity] = lambda: SimpleNamespace(username="a")
    app.dependency_overrides[timetable.get_storage] = lambda: SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    today = datetime.now(timezone(timedelta(hours=8))).date()
    copies = [{"source": (today + timedelta(days=1)).isoformat(), "target": today.isoformat()}]
    with TestClient(app) as client:
        url = "/api/timetable/agenda?term_code=2026-2027-1"
        created = client.put(url, json={"events": [event(), event(id="other", title="另一条日程")], "moves": copies})
        assert created.status_code == 200
        payload = {key: created.json()[key] for key in ("revision", "events", "moves")}
        original_other = dict(payload["events"][1])
        payload["events"][0].update(title="已修改", location="202", note="更新备注", important="新重点", start_time="10:00", end_time="11:00")
        edited = client.put(url, json=payload)
        assert edited.status_code == 200
        assert edited.json()["events"][0]["id"] == "event-1"
        assert edited.json()["events"][0]["title"] == "已修改"
        assert edited.json()["events"][1] == original_other
        payload = {key: edited.json()[key] for key in ("revision", "events", "moves")}
        payload["events"] = [original_other]
        deleted = client.put(url, json=payload)
        assert deleted.status_code == 200
        assert client.get(url).json()["events"] == [original_other]
        assert client.get(url).json()["moves"] == copies
