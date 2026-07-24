"""Windows desktop launcher for the frozen local application."""

from __future__ import annotations

import atexit
import ctypes
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


APP_MUTEX_NAME = "Local\\NEU-JWXT-Toolkit-Desktop"
_mutex_handle = None


def _desktop_root() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return root / "NEU-JWXT-Toolkit"


def _instance_file() -> Path:
    return _desktop_root() / "desktop-instance.json"


def _existing_url() -> str | None:
    try:
        data = json.loads(_instance_file().read_text(encoding="utf-8"))
        url = str(data["url"])
        with urllib.request.urlopen(f"{url}/api/health", timeout=1.5) as response:
            if response.status == 200:
                return url
    except (OSError, ValueError, KeyError):
        return None
    return None


def _acquire_mutex() -> bool:
    global _mutex_handle
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
    if not handle:
        return False
    _mutex_handle = handle
    # ERROR_ALREADY_EXISTS
    return ctypes.get_last_error() != 183


def _choose_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _open_when_ready(url: str) -> None:
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=0.5) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.125)


def main() -> int:
    # Windowed PyInstaller executables intentionally have no console streams.
    # Give application loggers a harmless sink so an incidental log record can
    # never abort the background service.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    if not _acquire_mutex():
        for _ in range(20):
            url = _existing_url()
            if url:
                if os.environ.get("NEU_JWXT_NO_BROWSER") != "1":
                    webbrowser.open(url)
                return 0
            time.sleep(0.1)
        return 1

    port = _choose_port()
    url = f"http://127.0.0.1:{port}"
    data_dir = _desktop_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    os.environ["NEU_JWXT_PROFILE"] = "desktop"
    os.environ["NEU_JWXT_DATA_DIR"] = str(data_dir)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = str(port)

    instance_file = _instance_file()
    instance_file.parent.mkdir(parents=True, exist_ok=True)
    instance_file.write_text(
        json.dumps({"pid": os.getpid(), "url": url}),
        encoding="utf-8",
    )

    def cleanup() -> None:
        try:
            current = json.loads(instance_file.read_text(encoding="utf-8"))
            if current.get("pid") == os.getpid():
                instance_file.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    atexit.register(cleanup)
    if os.environ.get("NEU_JWXT_NO_BROWSER") != "1":
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()

    import uvicorn
    from backend.app.main import app

    # A PyInstaller windowed executable has no stdout/stderr. Uvicorn's default
    # formatter probes stderr.isatty(), which crashes before the server starts.
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
