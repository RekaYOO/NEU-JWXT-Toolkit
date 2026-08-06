"""Architecture fitness functions for the application adapter boundary."""

from pathlib import Path

import pytest

from backend.app.dependencies import ApplicationServices, get_application_services


ROOT = Path(__file__).resolve().parents[2]


def test_application_services_owns_background_lifecycle():
    calls: list[tuple[str, object]] = []

    class Coordinator:
        def start(self):
            calls.append(("cache.start", None))

        def shutdown(self, **kwargs):
            calls.append(("cache.shutdown", kwargs))

    class Tracker:
        def start(self):
            calls.append(("tracking.start", None))

        def stop(self):
            calls.append(("tracking.stop", None))

    current = get_application_services()
    services = ApplicationServices(
        auth_sessions=current.auth_sessions,
        storage=current.storage,
        log_config=current.log_config,
        log_manager=current.log_manager,
        api_logger=current.api_logger,
        cache_registry=current.cache_registry,
        cache_store=current.cache_store,
        cache_coordinator=Coordinator(),
        grade_tracker=Tracker(),
        report_storage=current.report_storage,
        research_storage=current.research_storage,
    )

    services.start()
    services.shutdown(timeout=3)

    assert calls == [
        ("cache.start", None),
        ("tracking.start", None),
        ("tracking.stop", None),
        (
            "cache.shutdown",
            {"wait": True, "cancel_queued": True, "timeout": 3},
        ),
    ]


def _application_services(cache_coordinator, grade_tracker):
    current = get_application_services()
    return ApplicationServices(
        auth_sessions=current.auth_sessions,
        storage=current.storage,
        log_config=current.log_config,
        log_manager=current.log_manager,
        api_logger=current.api_logger,
        cache_registry=current.cache_registry,
        cache_store=current.cache_store,
        cache_coordinator=cache_coordinator,
        grade_tracker=grade_tracker,
        report_storage=current.report_storage,
        research_storage=current.research_storage,
    )


def test_application_services_rolls_back_partial_start():
    calls = []

    class Coordinator:
        def start(self):
            calls.append("cache.start")

        def shutdown(self, **_kwargs):
            calls.append("cache.shutdown")

    class Tracker:
        @staticmethod
        def start():
            raise RuntimeError("tracking start failed")

    services = _application_services(Coordinator(), Tracker())

    with pytest.raises(RuntimeError, match="tracking start failed"):
        services.start()

    assert calls == ["cache.start", "cache.shutdown"]


def test_application_services_always_closes_cache_on_shutdown_error():
    calls = []

    class Coordinator:
        def shutdown(self, **_kwargs):
            calls.append("cache.shutdown")

    class Tracker:
        @staticmethod
        def stop():
            raise RuntimeError("tracking stop failed")

    services = _application_services(Coordinator(), Tracker())

    with pytest.raises(RuntimeError, match="tracking stop failed"):
        services.shutdown()

    assert calls == ["cache.shutdown"]


def test_routers_do_not_import_private_helpers_from_sibling_routers():
    routers = ROOT / "backend" / "app" / "routers"
    offenders = []
    for path in routers.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "from backend.app.routers." in text:
            offenders.append(path.name)
    assert offenders == []


def test_migrated_routers_use_public_service_dependencies():
    routers = ROOT / "backend" / "app" / "routers"
    for name in (
        "tracking.py",
        "logs.py",
        "gpa.py",
        "report.py",
        "evaluation.py",
        "experiment.py",
    ):
        text = (routers / name).read_text(encoding="utf-8")
        assert "from backend.app.dependencies import _" not in text

    main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert "_cache_coordinator" not in main
    assert "_grade_tracker" not in main


def test_sensitive_core_modules_do_not_write_directly_to_stdout():
    checked = (
        ROOT / "backend" / "core" / "evaluation" / "api.py",
        ROOT / "backend" / "core" / "academic" / "experiment.py",
        ROOT / "backend" / "core" / "storage" / "storage.py",
    )
    assert all("print(" not in path.read_text(encoding="utf-8") for path in checked)
