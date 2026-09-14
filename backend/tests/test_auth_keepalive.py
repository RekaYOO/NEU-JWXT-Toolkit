import threading

from backend.core.auth.keepalive import AuthKeepaliveService


class Logger:
    def warning(self, *_args, **_kwargs):
        pass


def test_keepalive_runs_one_prompt_recovery_and_stops():
    called = threading.Event()
    calls = []

    def recover(**kwargs):
        calls.append(kwargs)
        called.set()

    service = AuthKeepaliveService(
        recover=recover,
        should_attempt=lambda: True,
        recovery_status=lambda: {"status": "authenticated"},
        logger=Logger(),
    )
    service.start()
    assert called.wait(timeout=1)
    service.stop()
    assert calls == [{"queue_timeout": 2.0}]


def test_keepalive_stays_local_when_logout_removed_all_identity():
    recover = []
    service = AuthKeepaliveService(
        recover=lambda **kwargs: recover.append(kwargs),
        should_attempt=lambda: False,
        recovery_status=lambda: {"status": "idle"},
        logger=Logger(),
    )
    service.start()
    service.stop()
    assert recover == []
