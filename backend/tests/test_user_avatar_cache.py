from datetime import datetime, timezone
from types import SimpleNamespace

from backend.app.routers import user as user_router
from backend.core.cache.resources import avatar_payload


def test_avatar_cache_route_never_submits_a_refresh(monkeypatch):
    payload = avatar_payload("avatar-token", b"PNG")
    entry = SimpleNamespace(
        payload=payload,
        revision="v1:avatar",
        saved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        last_checked_at=datetime(2026, 9, 1, 0, 1, tzinfo=timezone.utc),
    )
    calls = []
    monkeypatch.setattr(user_router, "read_cache", lambda account, resource: (calls.append((account, resource)) or (entry, False)))

    response = user_router.get_user_avatar_cache(SimpleNamespace(username="student"))

    assert response.body == b"PNG"
    assert response.headers["x-avatar-token"] == "avatar-token"
    assert response.headers["x-cache-stale"] == "false"
    assert calls == [("student", "avatar")]
