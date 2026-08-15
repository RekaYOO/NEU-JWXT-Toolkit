import threading
import time

from backend.core.auth.session_manager import AuthSessionManager


def test_remote_guard_prioritizes_mutation_over_queued_background_work():
    manager = AuthSessionManager()
    first_started = threading.Event()
    release_first = threading.Event()
    mutation_started = threading.Event()
    release_mutation = threading.Event()
    order: list[str] = []
    active = 0
    max_active = 0
    state_lock = threading.Lock()

    def enter(name: str, priority: str, started=None, release=None):
        nonlocal active, max_active
        with manager.remote_guard(priority=priority, label=name):
            with state_lock:
                active += 1
                max_active = max(max_active, active)
                order.append(name)
            if started:
                started.set()
            if release:
                assert release.wait(timeout=2)
            with state_lock:
                active -= 1

    first = threading.Thread(
        target=enter,
        args=("background-1", "background", first_started, release_first),
    )
    second = threading.Thread(
        target=enter,
        args=("background-2", "background"),
    )
    mutation = threading.Thread(
        target=enter,
        args=("mutation", "mutation", mutation_started, release_mutation),
    )
    first.start()
    assert first_started.wait(timeout=2)
    second.start()
    mutation.start()
    time.sleep(0.03)
    release_first.set()
    assert mutation_started.wait(timeout=2)
    assert order == ["background-1", "mutation"]
    release_mutation.set()

    for thread in (first, second, mutation):
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert order == ["background-1", "mutation", "background-2"]
    assert max_active == 1


def test_remote_guard_places_foreground_between_mutation_and_background():
    manager = AuthSessionManager()
    blocker_started = threading.Event()
    release_blocker = threading.Event()
    order: list[str] = []

    def run(name: str, priority: str, started=None, release=None):
        with manager.remote_guard(priority=priority, label=name):
            order.append(name)
            if started:
                started.set()
            if release:
                assert release.wait(timeout=2)

    blocker = threading.Thread(
        target=run,
        args=("blocker", "background", blocker_started, release_blocker),
    )
    background = threading.Thread(target=run, args=("background", "background"))
    foreground = threading.Thread(target=run, args=("foreground", "foreground"))
    mutation = threading.Thread(target=run, args=("mutation", "mutation"))
    blocker.start()
    assert blocker_started.wait(timeout=2)
    background.start()
    foreground.start()
    mutation.start()
    time.sleep(0.03)
    release_blocker.set()

    for thread in (blocker, background, foreground, mutation):
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert order == ["blocker", "mutation", "foreground", "background"]
