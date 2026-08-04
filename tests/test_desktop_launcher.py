import io
import json
from unittest.mock import Mock, patch

from launchers import desktop


class _HealthResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_existing_url_recovers_from_stable_port_when_instance_file_is_missing(tmp_path):
    payload = json.dumps({"status": "ok", "profile": "desktop"}).encode()
    with (
        patch.object(desktop, "_instance_file", return_value=tmp_path / "missing.json"),
        patch.object(desktop.urllib.request, "urlopen", return_value=_HealthResponse(payload)) as request,
    ):
        assert desktop._existing_url() == "http://127.0.0.1:18476"
    request.assert_called_once_with("http://127.0.0.1:18476/api/health", timeout=1.5)


def test_existing_url_rejects_non_desktop_service(tmp_path):
    payload = json.dumps({"status": "ok", "profile": "server"}).encode()
    with (
        patch.object(desktop, "_instance_file", return_value=tmp_path / "missing.json"),
        patch.object(desktop.urllib.request, "urlopen", return_value=_HealthResponse(payload)),
    ):
        assert desktop._existing_url() is None


def test_existing_url_rejects_non_loopback_instance_url(tmp_path):
    instance_file = tmp_path / "desktop-instance.json"
    instance_file.write_text(
        json.dumps({"url": "https://evil.example/desktop"}), encoding="utf-8"
    )
    payload = json.dumps({"status": "ok", "profile": "server"}).encode()
    with (
        patch.object(desktop, "_instance_file", return_value=instance_file),
        patch.object(desktop.urllib.request, "urlopen", return_value=_HealthResponse(payload)) as request,
    ):
        assert desktop._existing_url() is None
    request.assert_called_once_with("http://127.0.0.1:18476/api/health", timeout=1.5)


def test_service_url_rejects_unsafe_origin_shapes():
    for url in (
        "https://127.0.0.1:18476",
        "http://user@127.0.0.1:18476",
        "http://127.0.0.1:18476/path",
        "http://127.0.0.1:18476?query=1",
        "http://127.0.0.1:18476#fragment",
        "http://127.0.0.1:invalid",
    ):
        assert desktop._is_desktop_service(url) is False


def test_service_health_response_has_a_small_upper_bound():
    with patch.object(
        desktop.urllib.request,
        "urlopen",
        return_value=_HealthResponse(b" " * 4097),
    ):
        assert desktop._is_desktop_service("http://127.0.0.1:18476") is False


def test_choose_port_falls_back_when_stable_port_is_occupied():
    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def bind(self, address):
            if address[1] == desktop.DEFAULT_DESKTOP_PORT:
                raise OSError("occupied")

        def getsockname(self):
            return ("127.0.0.1", 21876)

    with patch.object(desktop.socket, "socket", return_value=FakeSocket()):
        assert desktop._choose_port() == 21876


def test_open_url_uses_windows_shell_before_browser_fallback():
    with (
        patch.object(desktop.os, "name", "nt"),
        patch.object(desktop.os, "startfile", create=True) as startfile,
        patch.object(desktop.webbrowser, "open") as browser,
    ):
        assert desktop._open_url("http://127.0.0.1:18476") is True
    startfile.assert_called_once_with("http://127.0.0.1:18476")
    browser.assert_not_called()


def test_tray_is_not_started_on_non_windows():
    with patch.object(desktop.os, "name", "posix"), patch.object(
        desktop.ctypes, "WinDLL", create=True
    ) as win_dll:
        desktop._run_tray("http://127.0.0.1:18476")
    win_dll.assert_not_called()


def test_tray_icon_prefers_packaged_brand_asset(tmp_path):
    icon = tmp_path / "app.ico"
    icon.write_bytes(b"icon")
    user32 = Mock()
    user32.LoadImageW.return_value = 123

    with patch.object(desktop, "_tray_icon_candidates", return_value=(icon,)):
        handle, owns_icon = desktop._load_tray_icon(user32)

    assert handle == 123
    assert owns_icon is True
    user32.LoadImageW.assert_called_once_with(
        None,
        str(icon),
        desktop.IMAGE_ICON,
        0,
        0,
        desktop.LR_LOADFROMFILE | desktop.LR_DEFAULTSIZE,
    )
    user32.LoadIconW.assert_not_called()


def test_tray_icon_falls_back_to_windows_default(tmp_path):
    user32 = Mock()
    user32.LoadIconW.return_value = 456

    with patch.object(
        desktop,
        "_tray_icon_candidates",
        return_value=(tmp_path / "missing.ico",),
    ):
        handle, owns_icon = desktop._load_tray_icon(user32)

    assert handle == 456
    assert owns_icon is False
    user32.LoadImageW.assert_not_called()
    user32.LoadIconW.assert_called_once()


def test_tray_icon_destroys_only_owned_handles():
    user32 = Mock()

    desktop._destroy_tray_icon(user32, 123, True)
    desktop._destroy_tray_icon(user32, 456, False)
    desktop._destroy_tray_icon(user32, None, True)

    user32.DestroyIcon.assert_called_once_with(123)
