"""Pure HTTP presentation helpers shared by online and offline adapters.

Routers must not import private helpers from sibling routers.  Keeping these
transformations here makes the online and offline contracts evolve together
without coupling route registration or request handling.
"""

from __future__ import annotations

from typing import Any, Protocol

from backend.app.schemas import CourseScoreModel


class ResearchFavorites(Protocol):
    def favorite_ids(self, username: str, batch_id: str) -> list[str]: ...

    def favorite_topics(self, username: str, snapshot: dict) -> list[dict]: ...


def _score_value(score: dict) -> float:
    try:
        number = float(score.get("score"))
        gpa = float(score.get("gpa") or 0)
        expected = (number - 50) / 10
        return number if abs(expected - gpa) < 0.3 else ((gpa + 5) * 10 if gpa else number)
    except (TypeError, ValueError):
        gpa = float(score.get("gpa") or 0)
        return (gpa + 5) * 10 if gpa else 0.0


def score_model(score: dict) -> CourseScoreModel:
    """Map an internal cached score to the stable public HTTP contract."""
    return CourseScoreModel(
        name=str(score.get("name") or ""),
        code=str(score.get("code") or ""),
        score=str(score.get("score") or ""),
        score_value=_score_value(score),
        gpa=float(score.get("gpa") or 0),
        credit=float(score.get("credit") or 0),
        term=str(score.get("term") or ""),
        term_display=str(score.get("term_display") or ""),
        course_type=str(score.get("course_type") or ""),
        course_category=str(score.get("course_category") or ""),
        general_category=str(score.get("general_category") or ""),
        exam_type=str(score.get("exam_type") or ""),
        exam_status=str(score.get("exam_status") or ""),
        course_nature=str(score.get("course_nature") or ""),
        is_passed=bool(score.get("is_passed")),
    )


def festival_remote_response(username: str, payload: dict) -> dict:
    """Remove remote-only and sensitive fields before returning activities."""
    public_fields = {
        "id", "section", "name", "team_name", "status", "category", "type",
        "award", "sign_in", "sign_out", "certificate_available",
        "registration_time", "activity_time", "start_time", "duration",
        "department", "location", "notes", "description",
    }
    activities = [
        {key: value for key, value in item.items() if key in public_fields}
        for item in (payload.get("activities") or [])
        if isinstance(item, dict)
    ]
    return {
        "available": True,
        "username": username,
        "source": "remote",
        "activities": activities,
        "warnings": payload.get("warnings") or [],
        "total": len(activities),
        "cache": None,
    }


def festival_cache_response(
    username: str,
    entry: Any,
    stale: bool,
    source: str = "cache",
) -> dict:
    if not entry:
        return {
            "available": False,
            "username": username,
            "source": source,
            "activities": [],
            "warnings": [],
            "total": 0,
            "cache": None,
        }
    payload = entry.payload if isinstance(entry.payload, dict) else {}
    result = festival_remote_response(username, payload)
    result["source"] = source
    result["cache"] = entry.metadata(is_stale=stale)
    return result


def research_cache_response(
    favorites: ResearchFavorites,
    username: str,
    entry: Any,
    *,
    is_stale: bool,
    update_available: bool = False,
    changes: dict | None = None,
) -> dict:
    if not entry:
        return {
            "available": False,
            "favorite_topic_ids": [],
            "favorite_topics": [],
            "update_available": False,
            "changes": {},
        }
    snapshot = entry.payload
    batch = snapshot.get("batch") or {}
    batch_id = str(batch.get("batch_id") or "")
    topics = snapshot.get("topics") or []
    return {
        "available": True,
        "version": entry.schema_version,
        "username": username,
        "saved_at": entry.saved_at.isoformat(),
        "revision": entry.revision,
        **snapshot,
        "total": len(topics),
        "favorite_topic_ids": favorites.favorite_ids(username, batch_id),
        "favorite_topics": favorites.favorite_topics(username, snapshot),
        "update_available": update_available,
        "changes": changes or {
            "added": 0,
            "updated": 0,
            "removed": 0,
            "new_batch": False,
            "confirmed_changed": False,
        },
        "cache": entry.metadata(is_stale=is_stale),
    }
