import threading
import time

from backend.core.auth.session_manager import AuthSessionManager


def test_pending_login_candidate_does_not_replace_active_identity():
    manager = AuthSessionManager()
    active = object()
    candidate = object()
    manager.set_client(active)
    epoch = manager.epoch()

    manager.set_pending_client(candidate)

    assert manager.peek_client() is active
    assert manager.peek_pending_client() is candidate
    assert manager.epoch() == epoch
    assert manager.clear_pending_client(candidate) is candidate
    assert manager.peek_client() is active


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


def test_remote_read_guard_allows_bounded_parallel_reads():
    manager = AuthSessionManager()
    release = threading.Event()
    all_started = threading.Event()
    state_lock = threading.Lock()
    active = 0
    max_active = 0

    def read():
        nonlocal active, max_active
        with manager.remote_read_guard():
            with state_lock:
                active += 1
                max_active = max(max_active, active)
                if active == 4:
                    all_started.set()
            assert release.wait(timeout=2)
            with state_lock:
                active -= 1

    threads = [threading.Thread(target=read) for _ in range(5)]
    for thread in threads:
        thread.start()
    assert all_started.wait(timeout=2)
    time.sleep(0.03)
    assert max_active == 4
    release.set()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_exclusive_remote_guard_waits_for_parallel_reads_to_finish():
    manager = AuthSessionManager()
    readers_started = threading.Event()
    release_readers = threading.Event()
    writer_started = threading.Event()
    state_lock = threading.Lock()
    active_readers = 0

    def read():
        nonlocal active_readers
        with manager.remote_read_guard():
            with state_lock:
                active_readers += 1
                if active_readers == 2:
                    readers_started.set()
            assert release_readers.wait(timeout=2)

    def write():
        with manager.remote_guard(priority="mutation"):
            writer_started.set()

    readers = [threading.Thread(target=read) for _ in range(2)]
    for thread in readers:
        thread.start()
    assert readers_started.wait(timeout=2)
    writer = threading.Thread(target=write)
    writer.start()
    time.sleep(0.03)
    assert not writer_started.is_set()
    release_readers.set()
    assert writer_started.wait(timeout=2)
    for thread in (*readers, writer):
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_logout_queue_callback_fences_before_new_login_can_enter():
    manager = AuthSessionManager()
    blocker_started = threading.Event()
    release_blocker = threading.Event()
    logout_queued = threading.Event()
    order: list[str] = []

    def blocker():
        with manager.remote_guard(priority="background", label="old-read"):
            order.append("old-read")
            blocker_started.set()
            assert release_blocker.wait(timeout=2)

    def logout():
        with manager.remote_guard(
            priority="mutation",
            label="logout",
            on_queued=lambda: (order.append("identity-fenced"), logout_queued.set()),
        ):
            order.append("logout-cleanup")

    def login():
        with manager.remote_guard(priority="foreground", label="new-login"):
            order.append("new-login")

    blocker_thread = threading.Thread(target=blocker)
    logout_thread = threading.Thread(target=logout)
    login_thread = threading.Thread(target=login)
    blocker_thread.start()
    assert blocker_started.wait(timeout=2)
    logout_thread.start()
    assert logout_queued.wait(timeout=2)
    login_thread.start()
    time.sleep(0.03)

    assert order == ["old-read", "identity-fenced"]
    release_blocker.set()

    for thread in (blocker_thread, logout_thread, login_thread):
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert order == [
        "old-read",
        "identity-fenced",
        "logout-cleanup",
        "new-login",
    ]


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


def test_remote_guard_places_foreground_auth_ahead_of_foreground_reads():
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
    foreground = threading.Thread(target=run, args=("foreground", "foreground"))
    authentication = threading.Thread(
        target=run,
        args=("foreground-auth", "foreground_auth"),
    )
    blocker.start()
    assert blocker_started.wait(timeout=2)
    foreground.start()
    authentication.start()
    time.sleep(0.03)
    release_blocker.set()

    for thread in (blocker, foreground, authentication):
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert order == ["blocker", "foreground-auth", "foreground"]


def test_remote_guard_eventually_runs_tracking_during_background_pressure():
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
    backgrounds = [
        threading.Thread(target=run, args=(f"background-{index}", "background"))
        for index in range(12)
    ]
    tracking = threading.Thread(target=run, args=("tracking", "tracking"))
    blocker.start()
    assert blocker_started.wait(timeout=2)
    tracking.start()
    for thread in backgrounds:
        thread.start()
    time.sleep(0.03)
    release_blocker.set()

    for thread in (blocker, tracking, *backgrounds):
        thread.join(timeout=3)
        assert not thread.is_alive()

    assert order.index("tracking") <= 9
    assert order[-1] != "tracking"
