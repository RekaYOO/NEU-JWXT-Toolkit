"""Embedded FastAPI lifecycle used by the Android local application."""

from __future__ import annotations

import os
import socket
import threading
from pathlib import Path


_lock = threading.Lock()
_thread: threading.Thread | None = None
_server = None
_port: int | None = None
_identity: tuple[str, str, str, str] | None = None
_socket: socket.socket | None = None


def start(data_dir: str, resource_root: str, session_token: str, version: str) -> int:
    """Start one loopback server and return its selected port."""
    global _port, _server, _thread, _identity, _socket
    with _lock:
        identity = (str(Path(data_dir).resolve()), str(Path(resource_root).resolve()), session_token, version)
        if _identity is not None and identity != _identity:
            raise RuntimeError("Mobile runtime identity cannot change within a process")
        if _thread is not None and _thread.is_alive() and _port is not None:
            return _port

        if len(session_token) < 32:
            raise ValueError("mobile session token must contain at least 32 characters")
        root = Path(data_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        os.environ.update({
            "NEU_JWXT_PROFILE": "mobile",
            "NEU_JWXT_DATA_DIR": str(root),
            "NEU_JWXT_RESOURCE_ROOT": str(Path(resource_root).resolve()),
            "NEU_JWXT_MOBILE_TOKEN": session_token,
            "NEU_JWXT_VERSION": str(version),
            "HOST": "127.0.0.1",
        })

        from backend.app.main import app, runtime_config
        import uvicorn

        if not runtime_config.mobile_mode or runtime_config.mobile_session_token != session_token:
            raise RuntimeError("Mobile runtime was imported with an incompatible configuration")
        _socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _socket.bind(("127.0.0.1", 0))
        _port = int(_socket.getsockname()[1])
        _identity = identity
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=_port,
            access_log=False,
            log_level="warning",
            proxy_headers=False,
        )
        _server = uvicorn.Server(config)
        _thread = threading.Thread(
            target=_server.run, kwargs={"sockets": [_socket]},
            name="neu-mobile-api", daemon=True,
        )
        _thread.start()
        return _port


def stop() -> None:
    global _port, _server, _thread, _socket
    with _lock:
        if _server is not None:
            _server.should_exit = True
        thread = _thread
    if thread is not None:
        thread.join(timeout=10)
    with _lock:
        if thread is not None and thread.is_alive():
            raise RuntimeError("Mobile server did not stop before the deadline")
        if _socket is not None:
            _socket.close()
        _socket = None
        _port = None
        _server = None
        _thread = None
