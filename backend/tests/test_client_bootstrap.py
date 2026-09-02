from types import SimpleNamespace

from backend.app.routers import client as client_router


def test_client_bootstrap_is_local_and_safe_without_active_identity(monkeypatch):
    monkeypatch.setattr(client_router, "_active_identity", lambda: (None, ""))
    monkeypatch.setattr(
        client_router,
        "pending_auth_challenge_snapshot",
        lambda: {"required": False},
    )

    result = client_router.get_client_bootstrap()

    assert result["auth"]["is_logged_in"] is False
    assert result["timetable"] is None
    assert result["avatar"] is None
    assert result["cache_cursor"] == 0


def test_client_bootstrap_reuses_typed_local_snapshots(monkeypatch):
    identity = SimpleNamespace(
        username="20260001",
        is_logged_in=True,
        active_mode="webvpn",
    )
    monkeypatch.setattr(
        client_router,
        "_active_identity",
        lambda: (identity, identity.username),
    )
    monkeypatch.setattr(
        client_router,
        "timetable_bootstrap_snapshot",
        lambda account: {"terms": [{"code": "2026-2027-1"}], "current": "2026-2027-1", "personal": []},
    )
    monkeypatch.setattr(
        client_router,
        "_avatar_snapshot",
        lambda account: {"revision": "v1:avatar"},
    )
    monkeypatch.setattr(client_router._cache_store, "latest_event_cursor", lambda account: 7)

    result = client_router.get_client_bootstrap()

    assert result["auth"]["current_user"] == "20260001"
    assert result["auth"]["network_mode"] == "webvpn"
    assert result["timetable"]["current"] == "2026-2027-1"
    assert result["avatar"]["revision"] == "v1:avatar"
    assert result["cache_cursor"] == 7


def test_client_updates_combines_only_requested_local_channels(monkeypatch):
    identity = SimpleNamespace(username="20260001", is_logged_in=True, active_mode="direct")
    monkeypatch.setattr(client_router, "_active_identity", lambda: (identity, identity.username))
    monkeypatch.setattr(
        client_router,
        "cache_events_snapshot",
        lambda account, cursor, limit: {"events": [{"resource": "scores"}], "cursor": 9},
    )

    result = client_router.get_client_updates(cursor=8, channels="cache")

    assert result["cache"]["cursor"] == 9
    assert "pending_auth" not in result
