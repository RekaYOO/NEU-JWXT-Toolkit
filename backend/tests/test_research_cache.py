import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app import dependencies
from backend.app.routers import research as research_router
from backend.app.schemas.research import ResearchFavoriteRequest
from backend.core.storage.research import ResearchTrainingStorage


def _snapshot(*topics, batch_id="batch-1"):
    return {
        "batch": {"batch_id": batch_id, "name": f"批次 {batch_id}"},
        "eligibility": {"available": True, "gpa": "3.5", "major_rank": "12"},
        "topics": list(topics),
        "confirmed_topics": [],
    }


def _topic(topic_id, *, registered_count=0):
    return {
        "topic_id": topic_id,
        "title": f"课题 {topic_id}",
        "advisor_name": "示例导师",
        "registered_count": registered_count,
        "capacity": 2,
    }


def test_unfavorited_expired_topics_are_replaced_but_favorites_are_archived(tmp_path):
    storage = ResearchTrainingStorage(tmp_path)
    first = storage.save_snapshot(
        "20250001",
        _snapshot(_topic("favorite"), _topic("ordinary")),
    )
    storage.set_favorite(
        "20250001",
        first["batch"],
        first["topics"][0],
        True,
    )

    current, changed, changes = storage.update_snapshot(
        "20250001",
        _snapshot(_topic("new"), batch_id="batch-2"),
    )

    assert changed is True
    assert changes["new_batch"] is True
    assert [topic["topic_id"] for topic in current["topics"]] == ["new"]
    favorites = storage.favorite_topics("20250001", current)
    assert len(favorites) == 1
    assert favorites[0]["topic_id"] == "favorite"
    assert favorites[0]["expired"] is True
    assert all(topic["topic_id"] != "ordinary" for topic in favorites)


def test_unfavorite_removes_an_expired_topic_archive(tmp_path):
    storage = ResearchTrainingStorage(tmp_path)
    first = storage.save_snapshot("20250001", _snapshot(_topic("favorite")))
    storage.set_favorite(
        "20250001",
        first["batch"],
        first["topics"][0],
        True,
    )
    current = storage.save_snapshot(
        "20250001",
        _snapshot(_topic("new"), batch_id="batch-2"),
    )

    storage.set_favorite(
        "20250001",
        {"batch_id": "batch-1"},
        {"topic_id": "favorite"},
        False,
    )

    assert storage.favorite_topics("20250001", current) == []
    saved = json.loads(storage.favorites_path.read_text(encoding="utf-8"))
    assert "20250001" not in saved["users"]


def test_research_cache_and_favorites_are_isolated_by_username(tmp_path):
    storage = ResearchTrainingStorage(tmp_path)
    first = storage.save_snapshot("20250001", _snapshot(_topic("topic-1")))
    storage.set_favorite(
        "20250001",
        first["batch"],
        first["topics"][0],
        True,
    )

    assert storage.load_snapshot("20250002") is None
    assert storage.favorite_ids("20250002", "batch-1") == []
    assert storage.favorite_ids("20250001", "batch-1") == ["topic-1"]


def test_topic_field_changes_are_not_reported_as_new_topics(tmp_path):
    storage = ResearchTrainingStorage(tmp_path)
    storage.save_snapshot("20250001", _snapshot(_topic("topic-1")))

    _, changed, changes = storage.update_snapshot(
        "20250001",
        _snapshot(_topic("topic-1", registered_count=1)),
    )

    assert changed is True
    assert changes["added"] == 0
    assert changes["updated"] == 1


def test_first_sync_builds_a_baseline_without_update_prompt(tmp_path):
    storage = ResearchTrainingStorage(tmp_path)

    _, changed, changes = storage.update_snapshot(
        "20250001",
        _snapshot(_topic("topic-1")),
    )

    assert changed is False
    assert changes["added"] == 1
    assert changes["new_batch"] is False


def _entry(snapshot):
    return SimpleNamespace(
        payload=snapshot,
        schema_version=1,
        saved_at=datetime.now(timezone.utc),
        revision="v1:research",
        metadata=lambda *, is_stale: {
            "revision": "v1:research",
            "is_stale": is_stale,
        },
    )


def test_manual_refresh_uses_unified_job_and_returns_committed_snapshot(monkeypatch, tmp_path):
    storage = ResearchTrainingStorage(tmp_path)
    calls = []
    monkeypatch.setattr(research_router, "_research_storage", storage)
    monkeypatch.setattr(
        research_router, "submit_refresh",
        lambda *args, **kwargs: calls.append(True) or SimpleNamespace(job_id="job-1"),
    )
    monkeypatch.setattr(
        research_router, "wait_for_job",
        lambda _job_id: SimpleNamespace(changed=False, changes={}, error_kind=None),
    )
    entry = _entry(_snapshot(_topic("topic-1")))
    monkeypatch.setattr(
        research_router, "read_cache",
        lambda account, resource: (entry, False),
    )
    auth = SimpleNamespace(username="20250001", is_logged_in=True)

    first = research_router.refresh_research_training(auth)
    second = research_router.refresh_research_training(auth)

    assert first["available"] is True
    assert second["revision"] == first["revision"]
    assert len(calls) == 2


def test_same_topic_id_in_a_new_batch_does_not_inherit_favorite(tmp_path):
    storage = ResearchTrainingStorage(tmp_path)
    previous = storage.save_snapshot(
        "20250001",
        _snapshot(_topic("shared-topic"), batch_id="batch-1"),
    )
    storage.set_favorite(
        "20250001",
        previous["batch"],
        previous["topics"][0],
        True,
    )
    current = storage.save_snapshot(
        "20250001",
        _snapshot(_topic("shared-topic"), batch_id="batch-2"),
    )

    assert storage.favorite_ids("20250001", "batch-2") == []
    favorites = storage.favorite_topics("20250001", current)
    assert favorites[0]["favorite_batch_id"] == "batch-1"
    assert favorites[0]["expired"] is True


def test_invalid_cache_file_is_ignored(tmp_path):
    storage = ResearchTrainingStorage(tmp_path)
    storage.cache_path.write_text("[]", encoding="utf-8")

    assert storage.load_snapshot("20250001") is None


def test_pending_login_cannot_use_cached_identity(monkeypatch):
    pending = SimpleNamespace(username="20250001", is_logged_in=False)
    monkeypatch.setattr(dependencies, "peek_auth_client", lambda: pending)

    with pytest.raises(HTTPException) as error:
        dependencies.require_cached_auth_identity()

    assert error.value.status_code == 401
    authenticated = SimpleNamespace(username="20250001", is_logged_in=True)
    monkeypatch.setattr(dependencies, "peek_auth_client", lambda: authenticated)
    assert dependencies.require_cached_auth_identity() is authenticated


def test_unfavoriting_history_returns_current_batch_favorite_ids(
    monkeypatch,
    tmp_path,
):
    storage = ResearchTrainingStorage(tmp_path)
    current = storage.save_snapshot(
        "20250001",
        _snapshot(_topic("current-topic"), batch_id="batch-current"),
    )
    storage.set_favorite(
        "20250001",
        current["batch"],
        current["topics"][0],
        True,
    )
    storage.set_favorite(
        "20250001",
        {"batch_id": "batch-old", "name": "旧批次"},
        _topic("old-topic"),
        True,
    )
    monkeypatch.setattr(research_router, "_research_storage", storage)
    entry = _entry({
        "batch": current["batch"],
        "eligibility": current["eligibility"],
        "topics": current["topics"],
        "confirmed_topics": current["confirmed_topics"],
    })
    monkeypatch.setattr(
        research_router, "read_cache",
        lambda account, resource: (entry, False),
    )

    result = research_router.set_research_topic_favorite(
        ResearchFavoriteRequest(
            batch_id="batch-old",
            topic_id="old-topic",
            favorite=False,
        ),
        SimpleNamespace(username="20250001", is_logged_in=True),
    )

    assert result["favorite_topic_ids"] == ["current-topic"]
    assert [topic["topic_id"] for topic in result["favorite_topics"]] == [
        "current-topic"
    ]
