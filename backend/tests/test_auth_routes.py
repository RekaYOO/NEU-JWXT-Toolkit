import asyncio
import unittest
from unittest.mock import Mock, patch

from backend.app.routers import auth
from backend.app.schemas.auth import LoginRequest
from backend.core.auth.client import WebVPNRequiredError


class AuthRouteTests(unittest.TestCase):
    def test_direct_login_failure_requests_webvpn_qr(self):
        client = Mock()
        client.login.side_effect = WebVPNRequiredError("校内服务不可直连")

        with patch.object(auth, "NEUAuthClient", return_value=client):
            response = asyncio.run(
                auth.login(LoginRequest(username="20250001", password="not-used", network_mode="direct"))
            )

        self.assertFalse(response.success)
        self.assertTrue(response.requires_webvpn)
        self.assertEqual(response.network_mode, "webvpn")

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
