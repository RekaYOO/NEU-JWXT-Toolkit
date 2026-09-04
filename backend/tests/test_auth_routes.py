import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app.routers import auth
from backend.app import dependencies
from backend.app.schemas.auth import (
    LoginRequest,
    WebVPNPasswordStartRequest,
    WebVPNQRStartRequest,
)
from backend.core.auth.client import (
    LOGIN_ERR_WRONG_PWD,
    NEULoginError,
    WEBVPN_ERR_CAMPUS_NETWORK,
    WebVPNRequiredError,
    WebVPNLoginError,
)
from backend.core.auth.session_manager import AuthSessionManager


class AuthRouteTests(unittest.TestCase):
    def test_password_rejections_have_one_public_message(self):
        direct_client = Mock()
        direct_client.login.side_effect = NEULoginError(
            "账号不存在",
            error_type=LOGIN_ERR_WRONG_PWD,
        )
        with patch.object(auth, "NEUAuthClient", return_value=direct_client):
            direct = auth.login(
                LoginRequest(
                    username="20250001",
                    password="wrong-password",
                    network_mode="direct",
                )
            )

        webvpn_client = Mock()
        webvpn_client.start_webvpn_password_login.side_effect = NEULoginError(
            "账号不存在",
            error_type=LOGIN_ERR_WRONG_PWD,
        )
        with patch.object(auth, "NEUAuthClient", return_value=webvpn_client):
            webvpn = auth.start_webvpn_password_login(
                WebVPNPasswordStartRequest(
                    username="20250001",
                    password="wrong-password",
                )
            )

        self.assertEqual(direct.message, "账号或密码错误")
        self.assertEqual(webvpn["message"], "账号或密码错误")
        self.assertEqual(direct.error_code, "WRONG_PASSWORD")
        self.assertEqual(webvpn["error_code"], "WRONG_PASSWORD")

    def test_login_requests_normalize_browser_whitespace_and_full_width_digits(self):
        direct = LoginRequest(
            username=" ２０２５０００１ \n",
            password=" password-keeps-spaces ",
        )
        webvpn = WebVPNPasswordStartRequest(
            username="\t２０２５０００１ ",
            password=" password-keeps-spaces ",
        )

        self.assertEqual(direct.username, "20250001")
        self.assertEqual(webvpn.username, "20250001")
        self.assertEqual(direct.password, " password-keeps-spaces ")
        self.assertEqual(webvpn.password, " password-keeps-spaces ")

    def test_login_requests_reject_embedded_username_whitespace(self):
        with self.assertRaises(ValueError):
            LoginRequest(username="2025 0001", password="password")

    def test_failed_active_session_is_replaced_without_intermediate_identity_gap(self):
        manager = AuthSessionManager()
        active = SimpleNamespace(
            username="20250001",
            password="saved-password",
            is_logged_in=False,
            _webvpn_qr_flow=None,
            _webvpn_sms_flow=None,
            ensure_login=lambda: False,
        )
        replacement = SimpleNamespace(
            username="20250001",
            password="saved-password",
            is_logged_in=True,
            ensure_login=lambda: True,
            active_mode="direct",
        )
        manager.set_client(active)
        initial_epoch = manager.epoch()
        storage = Mock()
        storage.load_credentials.return_value = ("20250001", "saved-password")

        with (
            patch.object(dependencies, "_auth_sessions", manager),
            patch.object(dependencies, "_storage", storage),
            patch.object(dependencies, "NEUAuthClient", return_value=replacement),
            patch.object(dependencies, "schedule_login_bootstrap") as bootstrap,
            patch.object(dependencies, "log_security_event"),
        ):
            resolved = dependencies._get_auth_client_unlocked()

        self.assertIs(resolved, replacement)
        self.assertIs(manager.peek_client(), replacement)
        self.assertEqual(manager.epoch(), initial_epoch + 1)
        bootstrap.assert_called_once_with(replacement)

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

    def test_webvpn_password_campus_block_recommends_direct_route(self):
        client = Mock()
        client.start_webvpn_password_login.side_effect = WebVPNLoginError(
            "检测到校园网环境，请切换为校内直连",
            error_code=WEBVPN_ERR_CAMPUS_NETWORK,
        )
        with patch.object(auth, "NEUAuthClient", return_value=client):
            response = auth.start_webvpn_password_login(
                WebVPNPasswordStartRequest(
                    username="20250001",
                    password="not-used",
                    remember=False,
                )
            )

        self.assertFalse(response["success"])
        self.assertEqual(response["error_code"], WEBVPN_ERR_CAMPUS_NETWORK)
        self.assertIn("校内直连", response["suggestion"])

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

    def test_jwxk_qr_success_merges_gateway_without_replacing_primary(self):
        active = Mock(username="20250001", is_logged_in=True)
        candidate = Mock(username="20250001")
        candidate.poll_webvpn_qr_login.return_value = {
            "status": "authenticated", "username": "20250001",
            "target_service": "jwxk",
        }
        with (
            patch.object(auth, "peek_pending_auth_client", return_value=candidate),
            patch.object(auth, "peek_auth_client", return_value=active),
            patch.object(auth, "clear_pending_auth_client") as clear_pending,
            patch.object(auth, "set_auth_client") as set_client,
            patch.object(auth, "schedule_login_bootstrap") as bootstrap,
        ):
            response = auth.get_webvpn_qr_status(
                auth.WebVPNQRStatusRequest(flow_id="qr-flow")
            )

        self.assertTrue(response["success"])
        clear_pending.assert_called_once_with(candidate)
        active.adopt_webvpn_gateway_session.assert_called_once_with(candidate)
        set_client.assert_not_called()
        bootstrap.assert_not_called()

    def test_jwxk_password_recovery_targets_selection_service(self):
        active = Mock(username="20250001", is_logged_in=True)
        candidate = Mock(username="20250001", password="not-used")
        candidate.start_webvpn_password_login.return_value = {
            "status": "authenticated", "username": "20250001",
            "target_service": "jwxk",
        }
        with (
            patch.object(auth, "NEUAuthClient", return_value=candidate),
            patch.object(auth, "peek_auth_client", return_value=active),
            patch.object(auth, "set_auth_client") as set_client,
        ):
            response = auth.start_webvpn_password_login(
                WebVPNPasswordStartRequest(
                    username="20250001", password="not-used", remember=False,
                    target_service="jwxk",
                )
            )

        self.assertTrue(response["success"])
        candidate.start_webvpn_password_login.assert_called_once_with(target_service="jwxk")
        active.adopt_webvpn_gateway_session.assert_called_once_with(candidate)
        set_client.assert_not_called()

    def test_webvpn_qr_expiry_has_stable_flow_error_code(self):
        candidate = Mock(username="20250001")
        candidate.poll_webvpn_qr_login.return_value = {"status": "expired"}
        with (
            patch.object(auth, "peek_pending_auth_client", return_value=candidate),
            patch.object(auth, "clear_pending_auth_client"),
        ):
            response = auth.get_webvpn_qr_status(
                auth.WebVPNQRStatusRequest(flow_id="qr-flow")
            )
        self.assertTrue(response["success"])
        self.assertEqual(response["status"], "expired")
        self.assertEqual(response["error_code"], "WEBVPN_FLOW_EXPIRED")

    def test_stale_qr_poll_does_not_clear_newer_sms_flow(self):
        candidate = Mock(username="20250001")
        candidate._webvpn_sms_flow = {"id": "sms-flow"}
        candidate.poll_webvpn_qr_login.side_effect = WebVPNLoginError(
            "二维码登录流程不存在或已被替换",
            error_code="WEBVPN_FLOW_REPLACED",
        )
        with (
            patch.object(auth, "peek_pending_auth_client", return_value=candidate),
            patch.object(auth, "clear_pending_auth_client") as clear_pending,
        ):
            response = auth.get_webvpn_qr_status(
                auth.WebVPNQRStatusRequest(flow_id="old-qr")
            )
        self.assertFalse(response["success"])
        self.assertEqual(response["error_code"], "WEBVPN_FLOW_REPLACED")
        clear_pending.assert_not_called()

    def test_webvpn_password_sms_challenge_is_logged_as_pending(self):
        client = Mock()
        client._webvpn_sms_flow = {}
        client.start_webvpn_password_login.return_value = {
            "status": "sms_required",
            "flow_id": "sms-flow",
        }

        with (
            patch.object(auth, "NEUAuthClient", return_value=client),
            patch.object(auth, "set_auth_client") as set_active,
            patch.object(auth, "clear_pending_auth_client", return_value=None),
            patch.object(auth, "set_pending_auth_client") as set_pending,
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
        set_pending.assert_called_once_with(client)
        set_active.assert_not_called()
        security_log.assert_called_once()
        self.assertEqual(security_log.call_args.args[:2], ("webvpn_password_login", "pending"))

    def test_pending_password_challenge_does_not_replace_active_identity(self):
        active = Mock(is_logged_in=True)
        candidate = Mock()
        candidate._webvpn_sms_flow = {}
        candidate.start_webvpn_password_login.return_value = {
            "status": "sms_required",
            "flow_id": "sms-flow",
        }

        with (
            patch.object(auth, "NEUAuthClient", return_value=candidate),
            patch.object(auth, "peek_auth_client", return_value=active),
            patch.object(auth, "clear_pending_auth_client", return_value=None),
            patch.object(auth, "set_pending_auth_client") as set_pending,
            patch.object(auth, "set_auth_client") as set_active,
            patch.object(auth, "log_security_event"),
        ):
            response = auth.start_webvpn_password_login(
                WebVPNPasswordStartRequest(
                    username="20250001",
                    password="not-used",
                    remember=False,
                )
            )

        self.assertTrue(response["success"])
        set_pending.assert_called_once_with(candidate)
        set_active.assert_not_called()

    def test_saved_credentials_sms_challenge_is_reused_until_expiry(self):
        manager = AuthSessionManager()
        candidate = SimpleNamespace(
            username="20250001",
            password="saved-password",
            is_logged_in=False,
            active_mode="webvpn",
            _webvpn_qr_flow=None,
            _webvpn_sms_flow={
                "id": "sms-flow",
                "expires_at": 4102444800,
            },
            ensure_login=Mock(return_value=False),
        )
        cookie_candidate = SimpleNamespace(
            username="",
            password="",
            is_logged_in=False,
            active_mode="webvpn",
            _webvpn_qr_flow=None,
            _webvpn_sms_flow=None,
            ensure_login=Mock(return_value=False),
        )
        storage = Mock()
        storage.load_credentials.return_value = ("20250001", "saved-password")

        with (
            patch.object(dependencies, "_auth_sessions", manager),
            patch.object(dependencies, "_storage", storage),
            patch.object(
                dependencies,
                "NEUAuthClient",
                side_effect=[cookie_candidate, candidate],
            ) as client_class,
            patch.object(dependencies, "log_security_event"),
        ):
            self.assertIsNone(dependencies._get_auth_client_unlocked())
            self.assertIs(manager.peek_pending_client(), candidate)
            initial_client_count = client_class.call_count
            self.assertIsNone(dependencies._get_auth_client_unlocked())

        self.assertEqual(client_class.call_count, initial_client_count)
        candidate.ensure_login.assert_called_once_with()

    def test_expired_pending_challenge_allows_saved_session_recovery(self):
        manager = AuthSessionManager()
        expired = SimpleNamespace(
            is_logged_in=False,
            _webvpn_qr_flow=None,
            _webvpn_sms_flow={"id": "old", "expires_at": 1},
        )
        recovered = SimpleNamespace(
            username="20250001",
            password="",
            is_logged_in=True,
            active_mode="webvpn",
            _webvpn_qr_flow=None,
            _webvpn_sms_flow=None,
            ensure_login=Mock(return_value=True),
        )
        manager.set_pending_client(expired)
        storage = Mock()
        storage.load_credentials.return_value = None

        with (
            patch.object(dependencies, "_auth_sessions", manager),
            patch.object(dependencies, "_storage", storage),
            patch.object(dependencies, "NEUAuthClient", return_value=recovered),
            patch.object(dependencies, "schedule_login_bootstrap"),
            patch.object(dependencies, "log_security_event"),
        ):
            resolved = dependencies._get_auth_client_unlocked()

        self.assertIs(resolved, recovered)
        self.assertIsNone(manager.peek_pending_client())

    def test_failed_saved_credentials_recovery_enters_shared_cooldown(self):
        manager = AuthSessionManager()
        cookie_candidate = SimpleNamespace(
            username="",
            password="",
            is_logged_in=False,
            active_mode="direct",
            _webvpn_qr_flow=None,
            _webvpn_sms_flow=None,
            ensure_login=Mock(return_value=False),
        )
        password_candidate = SimpleNamespace(
            username="20250001",
            password="saved-password",
            is_logged_in=False,
            active_mode="direct",
            _webvpn_qr_flow=None,
            _webvpn_sms_flow=None,
            ensure_login=Mock(side_effect=NEULoginError("temporary failure")),
        )
        storage = Mock()
        storage.load_credentials.return_value = ("20250001", "saved-password")

        with (
            patch.object(dependencies, "_auth_sessions", manager),
            patch.object(dependencies, "_storage", storage),
            patch.object(
                dependencies,
                "NEUAuthClient",
                side_effect=[cookie_candidate, password_candidate],
            ) as client_class,
            patch.object(dependencies, "log_security_event"),
        ):
            self.assertIsNone(dependencies._get_auth_client_unlocked())
            calls_after_failure = client_class.call_count
            self.assertIsNone(dependencies._get_auth_client_unlocked())

        self.assertEqual(calls_after_failure, 2)
        self.assertEqual(client_class.call_count, calls_after_failure)
        self.assertTrue(manager.auth_recovery_backoff()["active"])
        self.assertEqual(manager.auth_recovery_backoff()["failures"], 1)

    def test_successful_identity_and_logout_clear_recovery_cooldown(self):
        manager = AuthSessionManager()
        manager.note_auth_recovery_failure("20250001", error_code="REQUEST_ERROR")
        self.assertFalse(manager.auth_recovery_allowed("20250001"))

        authenticated = SimpleNamespace(username="20250001", is_logged_in=True)
        manager.set_client(authenticated)
        self.assertTrue(manager.auth_recovery_allowed("20250001"))

        manager.note_auth_recovery_failure("20250001", error_code="REQUEST_ERROR")
        manager.fence_and_clear()
        self.assertTrue(manager.auth_recovery_allowed("20250001"))

    def test_sms_success_promotes_pending_candidate_once(self):
        candidate = Mock(username="20250001")
        candidate._webvpn_sms_flow = {"remember": False}
        candidate.verify_webvpn_sms_code.return_value = {
            "status": "authenticated",
            "username": "20250001",
        }
        with (
            patch.object(auth, "_webvpn_sms_client", return_value=candidate),
            patch.object(auth, "peek_auth_client", return_value=None),
            patch.object(auth, "peek_pending_auth_client", return_value=candidate),
            patch.object(auth, "clear_pending_auth_client") as clear_pending,
            patch.object(auth, "_save_webvpn_password_login") as save_login,
            patch.object(auth, "log_security_event"),
        ):
            response = auth.verify_webvpn_sms_code(
                auth.WebVPNSMSVerifyRequest(
                    flow_id="sms-flow",
                    code="123456",
                    trust_device=False,
                )
            )

        self.assertTrue(response["success"])
        clear_pending.assert_called_once_with(candidate)
        save_login.assert_called_once_with(candidate, False)

    def test_jwxk_sms_success_merges_gateway_without_replacing_primary(self):
        active = Mock(username="20250001", is_logged_in=True)
        candidate = Mock(username="20250001", password="not-used")
        candidate._webvpn_sms_flow = {
            "remember": True,
            "target_service": "jwxk",
        }
        candidate.verify_webvpn_sms_code.return_value = {
            "status": "authenticated",
            "username": "20250001",
            "target_service": "jwxk",
            "service_auth_state": "service_unavailable",
        }
        with (
            patch.object(auth, "_webvpn_sms_client", return_value=candidate),
            patch.object(auth, "peek_auth_client", return_value=active),
            patch.object(auth, "peek_pending_auth_client", return_value=candidate),
            patch.object(auth, "clear_pending_auth_client") as clear_pending,
            patch.object(auth, "set_auth_client") as set_client,
            patch.object(auth, "schedule_login_bootstrap") as bootstrap,
            patch.object(auth._auto_login, "save_login") as save_login,
            patch.object(auth, "log_security_event"),
        ):
            response = auth.verify_webvpn_sms_code(
                auth.WebVPNSMSVerifyRequest(
                    flow_id="sms-flow",
                    code="123456",
                    trust_device=False,
                )
            )

        self.assertTrue(response["success"])
        self.assertEqual(response["service_auth_state"], "service_unavailable")
        clear_pending.assert_called_once_with(candidate)
        active.adopt_webvpn_gateway_session.assert_called_once_with(candidate)
        self.assertEqual(active.password, "not-used")
        save_login.assert_called_once_with(active)
        set_client.assert_not_called()
        bootstrap.assert_not_called()

    def test_pending_auth_endpoint_exposes_only_safe_challenge_fields(self):
        client = SimpleNamespace(
            _webvpn_sms_flow={
                "id": "flow-1",
                "source": "password",
                "captcha_image": "abc",
                "captcha_media_type": "image/jpeg",
                "expires_at": 4102444800,
            }
        )
        with patch.object(auth, "peek_auth_client", return_value=client), patch.object(
            auth, "peek_pending_auth_client", return_value=None
        ):
            response = auth.get_pending_auth_challenge()
        self.assertTrue(response["required"])
        self.assertEqual(response["flow_id"], "flow-1")
        self.assertEqual(response["captcha_image"], "data:image/jpeg;base64,abc")
        self.assertNotIn("ocr_candidate", response)
        self.assertNotIn("password", response)

    def test_sms_flow_errors_include_stable_error_code_and_legacy_message(self):
        with patch.object(auth, "_webvpn_sms_client", return_value=None):
            response = auth.send_webvpn_sms_code(
                auth.WebVPNSMSSendRequest(flow_id="missing", captcha_code="1234")
            )
        self.assertFalse(response["success"])
        self.assertEqual(response["status"], "missing")
        self.assertEqual(response["error_code"], "WEBVPN_FLOW_MISSING")
        self.assertIn("短信验证流程不存在", response["message"])

    def test_sms_upstream_error_preserves_specific_code(self):
        client = Mock()
        client.send_webvpn_sms_code.side_effect = WebVPNLoginError(
            "发送过于频繁，请稍后再试", error_code="WEBVPN_SMS_RATE_LIMITED"
        )
        with patch.object(auth, "_webvpn_sms_client", return_value=client):
            response = auth.send_webvpn_sms_code(
                auth.WebVPNSMSSendRequest(flow_id="flow", captcha_code="1234")
            )
        self.assertFalse(response["success"])
        self.assertEqual(response["error_code"], "WEBVPN_SMS_RATE_LIMITED")

    def test_logout_uses_current_client_without_name_error(self):
        client = Mock()
        client.username = "20250001"
        client.session = Mock()
        clear_result = {"deleted_count": 2, "preserved_count": 0}
        calls = []

        @contextmanager
        def guarded_logout(*, priority, label, on_queued):
            calls.append(("guard", priority, label))
            on_queued()
            yield {}

        with (
            patch.object(auth, "peek_auth_client", side_effect=[client, None]),
            patch.object(auth, "peek_pending_auth_client", return_value=None),
            patch.object(
                auth,
                "logout_auth_client",
                side_effect=lambda **kwargs: calls.append(("fence", kwargs["clear_cache"])),
            ) as logout_client,
            patch.object(auth, "remote_session_guard", side_effect=guarded_logout),
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
        self.assertEqual(calls[:2], [
            ("guard", "mutation", "logout"),
            ("fence", True),
        ])

    def test_logout_revokes_authentication_that_finishes_after_initial_fence(self):
        original = Mock()
        original.username = "20250001"
        original.session = Mock()
        late = Mock()
        late.username = "20250001"
        late.session = Mock()
        calls = []

        @contextmanager
        def guarded_logout(*, priority, label, on_queued):
            calls.append(("queued", priority, label))
            on_queued()
            calls.append(("old-auth-finished",))
            yield {}

        with (
            patch.object(auth, "peek_auth_client", side_effect=[original, late]),
            patch.object(auth, "peek_pending_auth_client", return_value=None),
            patch.object(
                auth,
                "logout_auth_client",
                side_effect=lambda **kwargs: calls.append(("fence", kwargs["clear_cache"])),
            ) as logout_client,
            patch.object(auth, "remote_session_guard", side_effect=guarded_logout),
            patch.object(auth, "_auto_login"),
            patch.object(auth, "_storage") as storage,
        ):
            response = auth.logout(clear_data=False)

        self.assertTrue(response["success"])
        self.assertEqual(
            calls[:4],
            [
                ("queued", "mutation", "logout"),
                ("fence", False),
                ("old-auth-finished",),
                ("fence", False),
            ],
        )
        self.assertEqual(logout_client.call_count, 2)
        original.clear_cookies.assert_called_once_with()
        late.clear_cookies.assert_called_once_with()
        storage.clear_all_data.assert_not_called()


if __name__ == "__main__":
    unittest.main()
