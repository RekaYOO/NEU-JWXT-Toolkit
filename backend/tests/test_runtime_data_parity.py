"""Run the same persistence and HTTP contracts in fresh runtime processes."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = r"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app import dependencies
from backend.app.routers import auth, course_outline, offline
from backend.core.auth.client import DirectAccessError
from backend.core.cache import CacheKey, CacheStore
from backend.core.runtime import get_runtime_config
from backend.core.storage import Storage

root, phase = Path(sys.argv[1]).resolve(), sys.argv[2]
config = get_runtime_config()
storage = Storage()
coordinator = dependencies.get_cache_coordinator()
assert config.data_dir == root
assert Path(storage.config.data_dir).resolve() == root
assert coordinator.store.path.resolve() == root / "cache.db"
key = CacheKey("20240001", "scores")
payload = {"scores": [{"code": "TEST001", "name": "Synthetic course", "score": "90",
    "gpa": 4.0, "credit": 2.0, "term": "2026-2027-1", "is_passed": True}]}

with patch("requests.sessions.Session.request", side_effect=AssertionError("unexpected remote access")):
    if phase == "write":
        storage.save_credentials("20240001", "synthetic-password")
        spec = coordinator.registry.get("scores")
        coordinator.store.commit_success(
            key=key, schema_version=spec.schema_version,
            revision_algorithm_version=spec.revision_algorithm_version,
            payload_type=spec.payload_type, payload=payload, revision="v1:parity",
            dependency_revisions={}, changes={}, reason="test",
        )
    assert Storage().load_credentials() == ("20240001", "synthetic-password")
    entry = CacheStore(root / "cache.db").get(key)
    assert entry.payload == payload
    assert CacheStore(root / "cache.db").get(CacheKey("20240002", "scores")) is None
    assert offline.offline_status()["has_scores"]
    scores = offline.offline_scores()
    assert scores.scores[0].code == "TEST001"
    assert scores.cache["revision"] == "v1:parity"
    assert scores.source == "offline"

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(course_outline.router, prefix="/api")
    identity = SimpleNamespace(username="20240001", is_logged_in=True)
    app.dependency_overrides[dependencies.require_serialized_auth] = lambda: identity
    client = TestClient(app)
    failing_auth = Mock()
    failing_auth.login.side_effect = DirectAccessError("synthetic direct failure")
    with patch.object(auth, "NEUAuthClient", return_value=failing_auth):
        response = client.post("/api/login", json={
            "username": "20240001", "password": "synthetic-password", "network_mode": "direct",
        })
    assert response.status_code == 200
    assert response.json()["requires_webvpn"]
    assert response.json()["error_code"] == "DIRECT_ACCESS_FAILED"

    outline = Mock()
    outline.search.return_value = {"items": [{"course_code": "TEST001"}], "total": 1}
    with patch.object(course_outline, "CourseOutlineAPI", return_value=outline):
        for _ in range(2):
            response = client.post("/api/course-outlines/search", json={"page": 1, "page_size": 20})
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.json()["total"] == 1
        assert outline.search.call_count == 2
    assert coordinator.store.get(key).payload == payload
    assert not coordinator.store.list_entries(account_id=key.account_id, resource="course-outlines")
"""


@pytest.mark.parametrize("profile", ["development", "desktop", "server", "mobile"])
def test_profiles_share_cache_persistence_and_realtime_contracts(profile, tmp_path):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("NEU_JWXT_")}
    env.update({
        "NEU_JWXT_PROFILE": profile,
        "NEU_JWXT_DATA_DIR": str(tmp_path),
        "NEU_JWXT_MOBILE_TOKEN": "synthetic-" + "x" * 48,
        "HOST": "127.0.0.1",
    })
    for phase in ("write", "restart"):
        result = subprocess.run(
            [sys.executable, "-c", SCRIPT, str(tmp_path), phase],
            cwd=Path(__file__).resolve().parents[2], env=env,
            capture_output=True, text=True, timeout=40,
        )
        assert result.returncode == 0, result.stdout + result.stderr
