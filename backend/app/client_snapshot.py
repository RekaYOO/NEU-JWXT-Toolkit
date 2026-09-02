"""Local-only application snapshots shared by HTTP adapters."""

from __future__ import annotations

import re
import time
from typing import Any

from backend.app.dependencies import (
    _cache_coordinator,
    _cache_store,
    peek_auth_client,
    peek_pending_auth_client,
)
from backend.app.schemas.timetable import PersonalTimetableResponse, TimetableBootstrapResponse


def pending_auth_challenge_snapshot(clients: tuple[Any, ...] | None = None) -> dict:
    candidates = clients or (peek_pending_auth_client(), peek_auth_client())
    for client in candidates:
        flow = getattr(client, "_webvpn_sms_flow", None) if client else None
        if not flow:
            continue
        if time.time() >= float(flow.get("expires_at", 0)):
            return {"required": False}
        return {
            "required": True,
            "flow_id": flow.get("id"),
            "source": flow.get("source", "password"),
            "captcha_image": (
                f"data:image/png;base64,{flow['captcha_image']}"
                if flow.get("captcha_image") else None
            ),
            "ocr_candidate": flow.get("ocr_candidate", ""),
            "ocr_confidence": flow.get("ocr_confidence", 0.0),
            "expires_at": flow.get("expires_at"),
        }
    return {"required": False}


def cache_events_snapshot(account: str, after: int | None, *, limit: int = 100) -> dict:
    if after is None:
        return {"events": [], "cursor": _cache_store.latest_event_cursor(account)}
    events = _cache_store.events_after(account, after, limit=limit)
    items = [
        {
            "cursor": event.cursor,
            "resource": event.key.resource,
            "variant": event.key.variant,
            "previous_revision": event.previous_revision,
            "revision": event.revision,
            "changed": event.changed,
            "changes": (
                {"counts": dict(event.changes.get("counts") or {})}
                if event.key.resource == "scores"
                else {
                    key: value
                    for key, value in event.changes.items()
                    if key.endswith("_changed") or key == "initial"
                }
            ),
            "reason": event.reason,
            "created_at": event.created_at.isoformat(),
        }
        for event in events
    ]
    return {
        "events": items,
        "cursor": items[-1]["cursor"] if items else after,
    }


def _term_order_key(code: str):
    match = re.search(r"(20\d{2})[^0-9]+(20\d{2})[^0-9]+([12])", str(code or ""))
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return (0, 0, 0)


def _compatible(entry: Any, spec: Any) -> bool:
    return bool(
        entry
        and entry.schema_version == spec.schema_version
        and entry.revision_algorithm_version == spec.revision_algorithm_version
        and entry.payload_type == spec.payload_type
    )


def _personal_response(entry: Any, stale: bool) -> PersonalTimetableResponse | None:
    if entry is None:
        return None
    try:
        return PersonalTimetableResponse(
            **entry.payload,
            source="server",
            is_fresh=not stale,
            last_update=entry.saved_at,
            cache=entry.metadata(is_stale=stale),
        )
    except Exception:
        return None


def timetable_bootstrap_snapshot(account: str) -> TimetableBootstrapResponse:
    coordinator = _cache_coordinator
    index_result = coordinator.read_many(
        account_id=account,
        resources=(("timetable-index", "default"),),
    )
    index_entry, index_stale = index_result.get(("timetable-index", "default"), (None, True))
    index_spec = coordinator.registry.get("timetable-index")
    terms: list[dict] = []
    current = None
    if _compatible(index_entry, index_spec) and isinstance(index_entry.payload, dict):
        terms = list(index_entry.payload.get("terms") or [])
        current = index_entry.payload.get("current") or None
    ordered_terms = sorted(
        {str(item.get("code") or "") for item in terms if item.get("code")},
        key=_term_order_key,
    )
    next_term = ""
    if current in ordered_terms:
        position = ordered_terms.index(current)
        if position + 1 < len(ordered_terms):
            next_term = ordered_terms[position + 1]
    allowed = {value for value in (current, next_term) if value}
    personal_spec = coordinator.registry.get("personal-timetable")
    entries = coordinator.store.list_entries(account_id=account, resource="personal-timetable")
    personal_reads = coordinator.read_many(
        account_id=account,
        resources=tuple(("personal-timetable", entry.key.variant) for entry in entries),
    )
    snapshots: list[PersonalTimetableResponse] = []
    for entry_index, entry in enumerate(entries):
        term_code = str(entry.key.variant).removeprefix("term:")
        if allowed and term_code not in allowed:
            continue
        if not allowed and entry_index >= 2:
            break
        if not _compatible(entry, personal_spec):
            continue
        _, stale = personal_reads.get(("personal-timetable", entry.key.variant), (entry, True))
        response = _personal_response(entry, stale)
        if response is not None:
            snapshots.append(response)
    snapshots.sort(
        key=lambda item: (item.term_code != current, _term_order_key(item.term_code)),
        reverse=False,
    )
    return TimetableBootstrapResponse(
        terms=terms,
        current=current,
        index_cache=(
            index_entry.metadata(is_stale=index_stale)
            if index_entry is not None else {}
        ),
        personal=snapshots,
    )
