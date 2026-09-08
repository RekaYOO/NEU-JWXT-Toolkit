"""Instrumentation-only upstream stubs; never included in the application APK."""

from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app.main import app
from backend.app import dependencies
from backend.app.routers import auth, course_outline
from backend.core.auth.client import DirectAccessError
from backend.core.cache import CacheKey, CacheStore
from backend.core.runtime import get_runtime_config
from backend.core.storage import Storage
from backend.core.storage.storage import StorageConfig

_patches = ExitStack()
_original_overrides = app.dependency_overrides.copy()


def cleanup():
    app.dependency_overrides.clear()
    app.dependency_overrides.update(_original_overrides)
    _patches.close()


def _direct_login():
    sleep(11)
    raise DirectAccessError("synthetic direct timeout")


def _sms_verify(*args):
    sleep(11)
    return {"status": "authenticated", "username": "20240001"}


def _outlines(**kwargs):
    sleep(11)
    return {"items": [{"course_code": "TEST001", "course_name": "Synthetic outline"}],
            "total": 1, "page": 1, "page_size": 20}


direct = Mock()
direct.login.side_effect = _direct_login
sms = SimpleNamespace(
    username="20240001", active_mode="webvpn", is_logged_in=False, _webvpn_sms_flow={},
    verify_webvpn_sms_code=Mock(side_effect=_sms_verify),
)
outlines = Mock()
outlines.search.side_effect = _outlines
committed = Mock()
_patches.enter_context(patch.object(auth, "NEUAuthClient", return_value=direct))
_patches.enter_context(patch.object(auth, "_webvpn_sms_client", return_value=sms))
_patches.enter_context(patch.object(auth, "peek_auth_client", return_value=sms))
_patches.enter_context(patch.object(auth, "_commit_webvpn_login", committed))
_patches.enter_context(patch.object(course_outline, "CourseOutlineAPI", return_value=outlines))
app.dependency_overrides[dependencies.require_serialized_auth] = lambda: sms


def verify():
    direct.login.assert_called_once()
    sms.verify_webvpn_sms_code.assert_called_once()
    committed.assert_called_once()
    outlines.search.assert_called_once()
    root = get_runtime_config().data_dir
    assert dependencies.get_cache_coordinator().store.path.resolve() == root / "cache.db"
    # Exercise Android's actual sqlite3 and private-filesystem persistence without
    # injecting fake accounts into the running application's data or auth state.
    with TemporaryDirectory(dir=root) as directory:
        storage = Storage(StorageConfig(data_dir=directory))
        storage.save_credentials("20240001", "synthetic-password")
        storage.save_config({"network_mode": "webvpn"})
        reopened = Storage(StorageConfig(data_dir=directory))
        assert reopened.load_credentials() == ("20240001", "synthetic-password")
        assert reopened.load_config()["network_mode"] == "webvpn"
        store = CacheStore(Path(directory) / "cache.db")
        spec = dependencies.get_cache_coordinator().registry.get("scores")
        key = CacheKey("20240001", "scores")
        store.commit_success(
            key=key, schema_version=spec.schema_version,
            revision_algorithm_version=spec.revision_algorithm_version,
            payload_type=spec.payload_type, payload={"scores": [{"code": "TEST001"}]},
            revision="v1:parity", changes={}, dependency_revisions={}, reason="test",
        )
        reopened_cache = CacheStore(store.path)
        assert reopened_cache.get(key).payload["scores"][0]["code"] == "TEST001"
        assert reopened_cache.get(CacheKey("20240002", "scores")) is None
    return True
