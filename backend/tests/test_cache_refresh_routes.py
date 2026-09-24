from types import SimpleNamespace

from backend.app.routers import cache
from backend.core.cache.models import RefreshStatus


def test_experiment_courses_can_submit_registered_refresh(monkeypatch):
    calls = []

    def submit(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            status=RefreshStatus.STARTED,
            key=SimpleNamespace(resource=kwargs["resource"], variant=kwargs["variant"]),
            job_id="experiment-job",
            revision=None,
            is_stale=True,
        )

    monkeypatch.setattr(cache._cache_coordinator, "submit", submit)
    monkeypatch.setattr(cache, "get_auth_generation", lambda: 7)
    result = cache.refresh_cache_resource(
        "experiment-courses",
        variant="default",
        force=True,
        reason="manual",
        auth=SimpleNamespace(username="example", is_logged_in=True),
    )

    assert result["job_id"] == "experiment-job"
    assert calls == [{
        "account_id": "example",
        "resource": "experiment-courses",
        "variant": "default",
        "identity_epoch": 7,
        "force": True,
        "reason": "manual",
    }]
