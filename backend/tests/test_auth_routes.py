import asyncio
import unittest
from unittest.mock import Mock, patch

from backend.app.routers import auth
from backend.app.schemas.auth import (
    LoginRequest,
    WebVPNPasswordStartRequest,
    WebVPNQRStartRequest,
)
from backend.core.auth.client import WebVPNRequiredError


class AuthRouteTests(unittest.TestCase):
    def test_direct_login_failure_requests_webvpn_qr(self):
        client = Mock()
        client.login.side_effect = WebVPNRequiredError("校内服务不可直连")

        with patch.object(auth, "NEUAuthClient", return_value=client) as client_class:
            response = asyncio.run(
                auth.login(LoginRequest(username="20250001", password="not-used", network_mode="direct"))
            )

        self.assertFalse(response.success)
        self.assertTrue(response.requires_webvpn)
        self.assertEqual(response.network_mode, "webvpn")
        client_class.assert_called_once_with(
            "20250001",
            "not-used",
            cookie_file=auth.COOKIE_FILE,
            network_mode="direct",
            restore_session=False,
        )

    def test_explicit_webvpn_flows_start_without_restoring_session(self):
        qr_client = Mock()
        qr_client.start_webvpn_qr_login.return_value = {
            "flow_id": "qr-flow",
            "qr_content": "https://example.invalid/qr",
            "expires_in": 180,
        }
        with (
            patch.object(auth, "NEUAuthClient", return_value=qr_client) as client_class,
            patch.object(auth, "set_auth_client"),
        ):
            response = asyncio.run(
                auth.start_webvpn_qr_login(WebVPNQRStartRequest(username="20250001"))
            )

        self.assertTrue(response["success"])
        client_class.assert_called_once_with(
            username="20250001",
            cookie_file=auth.COOKIE_FILE,
            network_mode="webvpn",
            restore_session=False,
        )

        password_client = Mock()
        password_client.start_webvpn_password_login.return_value = {
            "status": "authenticated",
            "username": "20250001",
        }
        with (
            patch.object(auth, "NEUAuthClient", return_value=password_client) as client_class,
            patch.object(auth, "_save_webvpn_password_login"),
        ):
            response = asyncio.run(
                auth.start_webvpn_password_login(
                    WebVPNPasswordStartRequest(
                        username="20250001",
                        password="not-used",
                        remember=False,
                    )
                )
            )

        self.assertTrue(response["success"])
        client_class.assert_called_once_with(
            "20250001",
            "not-used",
            cookie_file=auth.COOKIE_FILE,
            network_mode="webvpn",
            restore_session=False,
        )

    def test_logout_uses_current_client_without_name_error(self):
        client = Mock()
        client.session = Mock()
        clear_result = {"deleted_count": 2, "preserved_count": 0}

        with (
            patch.object(auth, "peek_auth_client", return_value=client),
            patch.object(auth, "set_auth_client") as set_client,
            patch.object(auth, "_auto_login") as auto_login,
            patch.object(auth, "_storage") as storage,
        ):
            storage.clear_all_data.return_value = clear_result
            response = asyncio.run(auth.logout(clear_data=True))

        self.assertTrue(response["success"])
        self.assertTrue(response["data_cleared"])
        self.assertEqual(response["cleared_files"], 2)
        client.cancel_webvpn_qr_login.assert_called_once_with()
        client.clear_cookies.assert_called_once_with()
        client.session.cookies.clear.assert_called_once_with()
        set_client.assert_called_once_with(None)
        auto_login.clear_login.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
