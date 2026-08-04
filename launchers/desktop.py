"""Windows desktop launcher for the frozen local application."""

from __future__ import annotations

import atexit
import ctypes
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


APP_MUTEX_NAME = "Local\\NEU-JWXT-Toolkit-Desktop"
DEFAULT_DESKTOP_PORT = 18476
_mutex_handle = None


def _desktop_root() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return root / "NEU-JWXT-Toolkit"


def _instance_file() -> Path:
    return _desktop_root() / "desktop-instance.json"


def _is_desktop_service(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.port is None
        ):
            return False
    except ValueError:
        return False
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=1.5) as response:
            raw_payload = response.read(4097)
            if len(raw_payload) > 4096:
                return False
            payload = json.loads(raw_payload.decode("utf-8"))
            return response.status == 200 and payload.get("profile") == "desktop"
    except (OSError, ValueError, KeyError, UnicodeDecodeError):
        return False


def _existing_url() -> str | None:
    candidates: list[str] = []
    try:
        data = json.loads(_instance_file().read_text(encoding="utf-8"))
        candidates.append(str(data["url"]))
    except (OSError, ValueError, KeyError):
        pass
    # The stable port is also a recovery channel if the small instance file was
    # removed while the desktop process kept running.
    candidates.append(f"http://127.0.0.1:{DEFAULT_DESKTOP_PORT}")
    for url in dict.fromkeys(candidates):
        if _is_desktop_service(url):
            return url
    return None


def _open_url(url: str) -> bool:
    """Open through the Windows shell, with stdlib browser lookup as fallback."""
    if os.name == "nt":
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        except OSError:
            pass
    return bool(webbrowser.open(url))


def _run_tray(url: str, request_shutdown=lambda: None) -> None:
    """Run a minimal native Windows notification-area icon and context menu."""
    if os.name != "nt":
        return

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    wm_command = 0x0111
    wm_destroy = 0x0002
    wm_lbuttondblclk = 0x0203
    wm_rbuttonup = 0x0205
    tray_message = 0x8000 + 20
    command_open = 1001
    command_exit = 1002
    nim_add = 0x00000000
    nim_delete = 0x00000002
    nif_message = 0x00000001
    nif_icon = 0x00000002
    nif_tip = 0x00000004
    mf_string = 0x00000000
    mf_separator = 0x00000800
    tpm_rightbutton = 0x0002
    tpm_returncmd = 0x0100
    idi_application = 32512

    wndproc_type = ctypes.WINFUNCTYPE(
        wintypes.LPARAM,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", wndproc_type),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]

    user32.DefWindowProcW.argtypes = (
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )
    user32.DefWindowProcW.restype = wintypes.LPARAM
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = (
        wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR
    )
    user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.TrackPopupMenu.argtypes = (
        wintypes.HMENU,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        ctypes.c_void_p,
    )
    user32.DestroyMenu.argtypes = (wintypes.HMENU,)
    user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
    user32.CreateWindowExW.argtypes = (
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        ctypes.c_void_p,
    )
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = (wintypes.HWND,)
    user32.PostQuitMessage.argtypes = (ctypes.c_int,)
    user32.GetMessageW.argtypes = (
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
    )
    user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
    user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
    user32.LoadIconW.argtypes = (wintypes.HINSTANCE, ctypes.c_void_p)
    user32.LoadIconW.restype = wintypes.HICON
    shell32.Shell_NotifyIconW.argtypes = (
        wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)
    )
    kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    icon_data = NOTIFYICONDATAW()

    @wndproc_type
    def window_proc(hwnd, message, wparam, lparam):
        if message == tray_message:
            if lparam == wm_lbuttondblclk:
                _open_url(url)
                return 0
            if lparam == wm_rbuttonup:
                menu = user32.CreatePopupMenu()
                if menu:
                    point = wintypes.POINT()
                    user32.AppendMenuW(menu, mf_string, command_open, "打开教务工具箱")
                    user32.AppendMenuW(menu, mf_separator, 0, None)
                    user32.AppendMenuW(menu, mf_string, command_exit, "退出程序")
                    user32.GetCursorPos(ctypes.byref(point))
                    user32.SetForegroundWindow(hwnd)
                    command = user32.TrackPopupMenu(
                        menu,
                        tpm_rightbutton | tpm_returncmd,
                        point.x,
                        point.y,
                        0,
                        hwnd,
                        None,
                    )
                    user32.DestroyMenu(menu)
                    if command == command_open:
                        _open_url(url)
                    elif command == command_exit:
                        request_shutdown()
                return 0
        if message == wm_command:
            return 0
        if message == wm_destroy:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    instance = kernel32.GetModuleHandleW(None)
    class_name = f"NEUJWXTToolkitTray-{os.getpid()}"
    window_class = WNDCLASSW()
    window_class.lpfnWndProc = window_proc
    window_class.hInstance = instance
    window_class.lpszClassName = class_name
    if not user32.RegisterClassW(ctypes.byref(window_class)):
        return
    hwnd = user32.CreateWindowExW(
        0, class_name, "NEU 教务工具箱", 0, 0, 0, 0, 0, None, None, instance, None
    )
    if not hwnd:
        return

    icon_data.cbSize = ctypes.sizeof(icon_data)
    icon_data.hWnd = hwnd
    icon_data.uID = 1
    icon_data.uFlags = nif_message | nif_icon | nif_tip
    icon_data.uCallbackMessage = tray_message
    icon_data.hIcon = user32.LoadIconW(None, ctypes.c_void_p(idi_application))
    icon_data.szTip = "NEU 教务工具箱（双击打开）"
    if not shell32.Shell_NotifyIconW(nim_add, ctypes.byref(icon_data)):
        user32.DestroyWindow(hwnd)
        return

    message = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
    finally:
        shell32.Shell_NotifyIconW(nim_delete, ctypes.byref(icon_data))
        user32.DestroyWindow(hwnd)


def _acquire_mutex() -> bool:
    global _mutex_handle
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (
        ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR
    )
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
    if not handle:
        return False
    # ERROR_ALREADY_EXISTS
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(handle)
        return False
    _mutex_handle = handle
    return True


def _release_mutex() -> None:
    global _mutex_handle
    if os.name != "nt" or not _mutex_handle:
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle(_mutex_handle)
    _mutex_handle = None


def _choose_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", DEFAULT_DESKTOP_PORT))
            return DEFAULT_DESKTOP_PORT
        except OSError:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])


def _open_when_ready(url: str) -> None:
    for _ in range(80):
        if _is_desktop_service(url):
            _open_url(url)
            return
        time.sleep(0.125)


def main() -> int:
    # Windowed frozen executables intentionally have no console streams.
    # Give application loggers a harmless sink so an incidental log record can
    # never abort the background service.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    if not _acquire_mutex():
        for _ in range(80):
            url = _existing_url()
            if url:
                if os.environ.get("NEU_JWXT_NO_BROWSER") != "1":
                    _open_url(url)
                return 0
            time.sleep(0.125)
        return 1

    port = _choose_port()
    url = f"http://127.0.0.1:{port}"
    data_dir = _desktop_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    os.environ["NEU_JWXT_PROFILE"] = "desktop"
    os.environ["NEU_JWXT_DATA_DIR"] = str(data_dir)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = str(port)
    os.environ["NEU_JWXT_SHUTDOWN_TOKEN"] = secrets.token_urlsafe(32)

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
        _release_mutex()

    atexit.register(cleanup)

    import uvicorn
    from backend.app.main import app

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_config=None,
            access_log=False,
        )
    )

    def request_shutdown() -> None:
        server.should_exit = True

    app.state.desktop_shutdown = request_shutdown
    if os.environ.get("NEU_JWXT_NO_TRAY") != "1":
        threading.Thread(
            target=_run_tray, args=(url, request_shutdown), daemon=True
        ).start()
    if os.environ.get("NEU_JWXT_NO_BROWSER") != "1":
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()

    # A windowed frozen executable has no stdout/stderr. Uvicorn's default
    # formatter probes stderr.isatty(), which crashes before the server starts.
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
