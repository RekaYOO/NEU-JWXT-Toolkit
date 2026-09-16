"""Account-owned date exceptions, independent of disposable timetable caches."""

import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading

from backend.core.runtime.config import secure_file

_lock = threading.RLock()


class AgendaConflict(ValueError):
    pass


class AgendaSemesterEnded(ValueError):
    pass


def agenda_today():
    return datetime.now(timezone(timedelta(hours=8))).date()


def semester_calendar(weeks):
    """Accept only a complete, contiguous Sunday-first official calendar."""
    try:
        rows = sorted(weeks, key=lambda row: int(row["number"]))
        if not rows or len(rows) > 30:
            return None
        previous = None
        for number, row in enumerate(rows, 1):
            start = date.fromisoformat(row["start_date"])
            end = date.fromisoformat(row["end_date"])
            if start.isoformat() != row["start_date"] or end.isoformat() != row["end_date"]:
                return None
            if int(row["number"]) != number or start.weekday() != 6 or end - start != timedelta(days=6):
                return None
            if previous is not None and start != previous + timedelta(days=1):
                return None
            previous = end
        return {"start": rows[0]["start_date"], "end": rows[-1]["end_date"]}
    except (KeyError, TypeError, ValueError):
        return None


def _read_record(path):
    if not path.exists():
        return {"revision": 0, "events": [], "moves": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _public_document(record):
    return {key: record[key] for key in ("revision", "events", "moves")}


def _ended(record, today):
    calendar = record.get("_semester") or {}
    return bool(record.get("_retired") or calendar.get("end", today.isoformat()) < today.isoformat())


def read_agenda(data_dir, account, term):
    path = _path(data_dir, account, term)
    with _lock:
        return _public_document(_read_record(path))


def read_agenda_state(data_dir, account, term, *, today=None):
    with _lock:
        record = _read_record(_path(data_dir, account, term))
        return {**_public_document(record), "semester_ended": _ended(record, today or agenda_today()),
                "semester_end": (record.get("_semester") or {}).get("end")}


def _path(data_dir, account, term):
    owner = hashlib.sha256(str(account).encode()).hexdigest()
    key = hashlib.sha256(str(term).encode()).hexdigest()
    return Path(data_dir) / "timetable_agendas" / owner / (key + ".json")


def save_agenda(data_dir, account, term, document, *, calendar=None, today=None):
    with _lock:
        path = _path(data_dir, account, term)
        current = _read_record(path)
        if document["revision"] != current["revision"]:
            raise AgendaConflict("日程已在其他窗口更新，请重新读取后操作")
        if _ended(current, today or agenda_today()):
            raise AgendaSemesterEnded("该学期已结束，日程不再开放编辑")
        result = {**document, "revision": current["revision"] + 1}
        if current.get("_semester"):
            result["_semester"] = current["_semester"]
        elif calendar:
            result["_semester"] = calendar
        if _ended(result, today or agenda_today()):
            raise AgendaSemesterEnded("该学期已结束，日程不再开放编辑")
        _write_record(path, result)
        return _public_document(result)


def reconcile_agenda_semesters(data_dir, account, calendars, current_term, *, today=None):
    """Retire content only after a newer official semester has actually begun."""
    today = today or agenda_today()
    current = calendars.get(current_term)
    started = current and current["start"] <= today.isoformat() <= current["end"]
    with _lock:
        # Only inspect this account; never create documents for unvisited terms.
        folder = _path(data_dir, account, "unused").parent
        paths = {path for path in folder.glob("*.json") if len(path.stem) == 64}
        by_path = {_path(data_dir, account, term): span for term, span in calendars.items()}
        for path in paths:
            try:
                record = _read_record(path)
                if not isinstance(record, dict):
                    continue
                before = json.dumps(record, sort_keys=True)
                span = by_path.get(path)
                old = record.get("_semester")
                if span and (not old or old["start"] == span["start"]):
                    # A truncated later response must not shorten retention.
                    record["_semester"] = {**span, "end": max(span["end"], (old or {}).get("end", ""))}
                known = record.get("_semester")
                if started and known and known["end"] < current["start"] and _ended(record, today) and not record.get("_retired"):
                    record.update(events=[], moves=[], revision=record["revision"] + 1, _retired=True)
                if json.dumps(record, sort_keys=True) != before:
                    _write_record(path, record)
            except (KeyError, TypeError, ValueError):
                # A damaged file must never be overwritten as an empty agenda.
                continue


def _write_record(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".agenda-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        secure_file(Path(name))
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
