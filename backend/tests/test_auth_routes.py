import unittest
from unittest.mock import Mock, patch

from backend.app.routers import auth
from backend.app import dependencies
from backend.app.schemas.auth import (
    LoginRequest,
    WebVPNPasswordStartRequest,
    WebVPNQRStartRequest,
)
from backend.core.auth.client import WebVPNRequiredError


class AuthRouteTests(unittest.TestCase):
    def test_saved_credentials_hydrate_cookie_restored_same_account(self):
        client = Mock(username="20250001", password="")
        storage = Mock()
        storage.load_credentials.return_value = ("20250001", "saved-password")

        with patch.object(dependencies, "_storage", storage):
            attached = dependencies.attach_saved_auth_credentials(client)

        self.assertTrue(attached)
        self.assertEqual(client.password, "saved-password")

    def test_direct_login_failure_requests_webvpn_qr(self):
        client = Mock()
        client.login.side_effect = WebVPNRequiredError("校内服务不可直连")

        with patch.object(auth, "NEUAuthClient", return_value=client) as client_class:
            response = auth.login(
                LoginRequest(username="20250001", password="not-used", network_mode="direct")
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
            patch.object(auth, "clear_pending_auth_client", return_value=None),
            patch.object(auth, "set_pending_auth_client") as set_pending,
        ):
            response = auth.start_webvpn_qr_login(
                WebVPNQRStartRequest(username="20250001")
            )

        self.assertTrue(response["success"])
        client_class.assert_called_once_with(
            username="20250001",
            cookie_file=auth.COOKIE_FILE,
            network_mode="webvpn",
            restore_session=False,
        )
        set_pending.assert_called_once_with(qr_client)

        password_client = Mock()
        password_client.start_webvpn_password_login.return_value = {
            "status": "authenticated",
            "username": "20250001",
        }
        with (
            patch.object(auth, "NEUAuthClient", return_value=password_client) as client_class,
            patch.object(auth, "_save_webvpn_password_login"),
            patch.object(auth, "log_security_event") as security_log,
        ):
            response = auth.start_webvpn_password_login(
                    WebVPNPasswordStartRequest(
                        username="20250001",
                        password="not-used",
                        remember=False,
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
        security_log.assert_called_once()
        self.assertEqual(security_log.call_args.args[:2], ("webvpn_password_login", "success"))

    def test_in_page_webvpn_qr_login_preserves_active_session_until_success(self):
        active_client = Mock()
        active_client.is_logged_in = True
        candidate = Mock()
        candidate.start_webvpn_qr_login.return_value = {
            "flow_id": "qr-flow",
            "qr_content": "https://example.invalid/qr",
            "expires_in": 180,
        }
        with (
            patch.object(auth, "peek_auth_client", return_value=active_client),
            patch.object(auth, "NEUAuthClient", return_value=candidate) as client_class,
            patch.object(auth, "set_auth_client") as set_client,
            patch.object(auth, "clear_pending_auth_client", return_value=None),
            patch.object(auth, "set_pending_auth_client") as set_pending,
        ):
            response = auth.start_webvpn_qr_login(
                WebVPNQRStartRequest()
            )

        self.assertTrue(response["success"])
        candidate.start_webvpn_qr_login.assert_called_once_with()
        client_class.assert_called_once()
        set_pending.assert_called_once_with(candidate)
        set_client.assert_not_called()

    def test_webvpn_qr_success_atomically_commits_candidate(self):
        candidate = Mock(username="20250001")
        candidate.poll_webvpn_qr_login.return_value = {
            "status": "authenticated",
            "username": "20250001",
        }
        with (
            patch.object(auth, "peek_pending_auth_client", return_value=candidate),
            patch.object(auth, "clear_pending_auth_client") as clear_pending,
            patch.object(auth, "set_auth_client") as set_client,
            patch.object(auth, "schedule_login_bootstrap") as bootstrap,
        ):
            response = auth.get_webvpn_qr_status(
                auth.WebVPNQRStatusRequest(flow_id="qr-flow")
            )

        self.assertTrue(response["success"])
        clear_pending.assert_called_once_with(candidate)
        set_client.assert_called_once_with(candidate, force_epoch=True)
        bootstrap.assert_called_once_with(candidate)

    def test_webvpn_password_sms_challenge_is_logged_as_pending(self):
        client = Mock()
        client._webvpn_sms_flow = {}
        client.start_webvpn_password_login.return_value = {
            "status": "sms_required",
            "flow_id": "sms-flow",
        }

        with (
            patch.object(auth, "NEUAuthClient", return_value=client),
            patch.object(auth, "set_auth_client"),
            patch.object(auth, "log_security_event") as security_log,
        ):
            response = auth.start_webvpn_password_login(
                WebVPNPasswordStartRequest(
                    username="20250001",
                    password="not-used",
                    remember=True,
                )
            )

        self.assertTrue(response["success"])
        self.assertEqual(client._webvpn_sms_flow["remember"], True)
        security_log.assert_called_once()
        self.assertEqual(security_log.call_args.args[:2], ("webvpn_password_login", "pending"))

    def test_logout_uses_current_client_without_name_error(self):
        client = Mock()
        client.session = Mock()
        clear_result = {"deleted_count": 2, "preserved_count": 0}

        with (
            patch.object(auth, "peek_auth_client", return_value=client),
            patch.object(auth, "logout_auth_client") as logout_client,
            patch.object(auth, "_auto_login") as auto_login,
            patch.object(auth, "_storage") as storage,
        ):
            storage.clear_all_data.return_value = clear_result
            response = auth.logout(clear_data=True)

        self.assertTrue(response["success"])
        self.assertTrue(response["data_cleared"])
        self.assertEqual(response["cleared_files"], 2)
        client.cancel_webvpn_qr_login.assert_called_once_with()
        client.clear_cookies.assert_called_once_with()
        client.session.cookies.clear.assert_called_once_with()
        logout_client.assert_called_once_with(clear_cache=True)
        auto_login.clear_login.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
