import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.core.auth.client import (
    LOGIN_ERR_WRONG_PWD,
    NEUAuthClient,
    NEULoginError,
    WebVPNLoginError,
    _public_login_error_message,
)


class WebVPNSMSLoginTests(unittest.TestCase):
    def _response(self, url, text="", json_data=None, content=None, content_type=None):
        response = Mock()
        response.url = url
        response.text = text
        response.content = content if content is not None else text.encode("utf-8")
        response.headers = {
            "Content-Type": content_type or ("application/json" if json_data is not None else "text/html")
        }
        response.raise_for_status.return_value = None
        response.json.return_value = json_data or {}
        return response

    def test_sms_flow_uses_official_second_auth_requests_then_resubmits_form(self):
        login_url = "https://webvpn.neu.edu.cn/https/token/tpass/login?service=test"
        login_page = self._response(
            login_url,
            '<form id="loginForm" action=""><input type="hidden" name="lt" value="ticket"></form>',
        )
        sms_page = self._response(
            login_url,
            """
            <form id="second_auth_form" action="/tpass/secondAuth">
              <input name="RelayState" value="relay">
              <input name="execution" value="exec">
              <input name="_eventId" value="submit">
              <input name="method" value="mobile">
              <input name="imgCode" value="">
              <img src="/static/images/captcha-preview.jpg">
            </form>
            """,
        )
        captcha = self._response(
            "https://webvpn.neu.edu.cn/https/token/tpass/code?vpn-1&0.8967832515796772",
            content=b"\xff\xd8\xffcaptcha",
            content_type="image/jpeg",
        )
        sms_sent = self._response(login_url, json_data={"info": "send"})
        completed = self._response("https://webvpn.neu.edu.cn/login")

        client = NEUAuthClient("20250001", "secret", network_mode="webvpn")
        client._session = Mock()
        client.session.get.side_effect = [login_page, captcha]
        client.session.post.side_effect = [sms_page, sms_sent, completed]

        with (
            patch.object(client, "_webvpn_health_check", return_value=True),
            patch.object(client, "_sync_cas_cookie_to_webvpn"),
            patch("backend.core.auth.client.random.random", return_value=0.8967832515796772),
        ):
            started = client.start_webvpn_password_login()
            self.assertEqual(started["status"], "sms_required")
            self.assertEqual(started["expires_in"], 300)
            self.assertTrue(started["captcha_image"].startswith("data:image/jpeg;base64,"))
            self.assertNotIn("ocr_candidate", started)
            self.assertEqual(client.send_webvpn_sms_code(started["flow_id"], "1234")["status"], "sent")
            completed_result = client.verify_webvpn_sms_code(started["flow_id"], "123456")

        self.assertEqual(completed_result["status"], "authenticated")
        post_calls = client.session.post.call_args_list
        self.assertEqual(
            post_calls[1].args[0],
            "https://webvpn.neu.edu.cn/https/token/tpass/secondAuthCode?vpn-12-o2-pass.neu.edu.cn",
        )
        self.assertEqual(post_calls[1].kwargs["data"], {"code": "1234", "method": "mobile"})
        self.assertEqual(post_calls[2].kwargs["data"]["imgCode"], "1234")
        self.assertEqual(post_calls[2].kwargs["data"]["scendAuthCode"], "123456")
        self.assertEqual(post_calls[2].kwargs["data"]["method"], "mobile")
        self.assertEqual(post_calls[2].kwargs["data"]["RelayState"], "relay")
        self.assertEqual(post_calls[2].kwargs["data"]["execution"], "exec")
        self.assertEqual(post_calls[2].kwargs["data"]["_eventId"], "submit")
        self.assertEqual(
            client.session.get.call_args_list[1].args[0],
            "https://webvpn.neu.edu.cn/https/token/tpass/code?vpn-1&0.8967832515796772",
        )
        captcha_headers = client.session.get.call_args_list[1].kwargs["headers"]
        self.assertEqual(captcha_headers["Referer"], login_url)
        self.assertIn("image/*", captcha_headers["Accept"])

    def test_account_or_password_rejections_use_one_clear_public_message(self):
        for official_message in (
            "账号不存在",
            "密码错误",
            "用户名或密码错误",
        ):
            self.assertEqual(
                _public_login_error_message(
                    official_message,
                    LOGIN_ERR_WRONG_PWD,
                ),
                "账号或密码错误",
            )

    def test_webvpn_password_rejection_does_not_expose_account_not_found(self):
        login_url = "https://webvpn.neu.edu.cn/https/token/tpass/login?service=test"
        login_page = self._response(
            login_url,
            '<form id="loginForm"><input name="lt" value="ticket"></form>',
        )
        rejected = self._response(login_url, '<div id="errormsg">账号不存在</div>')
        client = NEUAuthClient("20250001", "wrong-password", network_mode="webvpn")

        with (
            patch.object(
                client,
                "_open_webvpn_password_page",
                return_value=(login_page, False),
            ),
            patch.object(client, "_submit_login_form", return_value=rejected),
            patch.object(client, "_extract_error_message", return_value="账号不存在"),
        ):
            with self.assertRaises(NEULoginError) as caught:
                client.start_webvpn_password_login()

        self.assertEqual(caught.exception.error_type, LOGIN_ERR_WRONG_PWD)
        self.assertEqual(str(caught.exception), "账号或密码错误")

    def test_explicit_login_can_skip_stale_persisted_cookies(self):
        with tempfile.TemporaryDirectory() as directory:
            cookie_file = Path(directory) / "session.json"
            cookie_file.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "username": "20250001",
                        "active_mode": "webvpn",
                        "cookies": [
                            {
                                "name": "stale_gateway",
                                "value": "stale",
                                "domain": ".webvpn.neu.edu.cn",
                                "path": "/",
                                "expires": None,
                                "secure": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            restored = NEUAuthClient(
                "20250001",
                cookie_file=str(cookie_file),
                network_mode="webvpn",
            )
            clean = NEUAuthClient(
                "20250001",
                cookie_file=str(cookie_file),
                network_mode="webvpn",
                restore_session=False,
            )

        self.assertIn("stale_gateway", {cookie.name for cookie in restored.session.cookies})
        self.assertNotIn("stale_gateway", {cookie.name for cookie in clean.session.cookies})

    def test_webvpn_entry_retries_once_after_clearing_unusable_cookies(self):
        unexpected_page = self._response("https://webvpn.neu.edu.cn/portal")
        login_page = self._response(
            "https://webvpn.neu.edu.cn/https/token/tpass/login?service=test"
        )
        client = NEUAuthClient("20250001", "secret", network_mode="webvpn")
        client._session = Mock()
        client.session.get.side_effect = [unexpected_page, login_page]

        with patch.object(client, "_webvpn_health_check", return_value=False):
            result, session_is_valid = client._open_webvpn_password_page()

        self.assertIs(result, login_page)
        self.assertFalse(session_is_valid)
        self.assertEqual(client.session.get.call_count, 2)
        client.session.cookies.clear.assert_called_once_with()

    def test_sms_send_accepts_numeric_zero_success_code(self):
        client = NEUAuthClient("20250001", "secret", network_mode="webvpn")
        client._webvpn_sms_flow = {"id": "flow", "page_url": "https://webvpn.neu.edu.cn/https/token/tpass/login", "expires_at": 4102444800}
        response = self._response(
            "https://webvpn.neu.edu.cn/https/token/tpass/secondAuthCode",
            json_data={"info": 0},
        )
        client._session = Mock()
        client.session.post.return_value = response
        with patch("backend.core.auth.client.time.time", return_value=1000):
            result = client.send_webvpn_sms_code("flow", "1234")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["expires_in"], 300)
        self.assertEqual(client._webvpn_sms_flow["code_expires_at"], 1300)
        self.assertEqual(client._webvpn_sms_flow["expires_at"], 1360)

    def test_sms_challenge_safe_view_reuses_current_captcha(self):
        client = NEUAuthClient("20250001", "secret", network_mode="webvpn")
        client._webvpn_sms_flow = {
            "id": "flow",
            "expires_at": 4102444800,
            "captcha_image": "YWJj",
            "captcha_media_type": "image/jpeg",
        }

        challenge = client.get_webvpn_sms_challenge()

        self.assertEqual(challenge["status"], "sms_required")
        self.assertEqual(challenge["flow_id"], "flow")
        self.assertEqual(challenge["captcha_image"], "data:image/jpeg;base64,YWJj")
        self.assertEqual(challenge["target_service"], "primary")
        self.assertNotIn("hidden_fields", challenge)

    def test_sms_send_non_json_response_has_stable_error_code(self):
        client = NEUAuthClient("20250001", "secret", network_mode="webvpn")
        client._webvpn_sms_flow = {"id": "flow", "page_url": "https://webvpn.neu.edu.cn/https/token/tpass/login", "expires_at": 4102444800}
        response = self._response(
            "https://webvpn.neu.edu.cn/https/token/tpass/secondAuthCode",
            text="<html>unexpected</html>",
        )
        response.json.side_effect = ValueError("not json")
        client._session = Mock()
        client.session.post.return_value = response
        with self.assertRaises(WebVPNLoginError) as caught:
            client.send_webvpn_sms_code("flow", "1234")
        self.assertEqual(caught.exception.error_code, "WEBVPN_UPSTREAM_NON_JSON")

    def test_malformed_sms_flow_is_expired_instead_of_raising_key_error(self):
        client = NEUAuthClient("20250001", "secret", network_mode="webvpn")
        client._webvpn_sms_flow = {"id": "flow"}
        with self.assertRaises(WebVPNLoginError) as caught:
            client.send_webvpn_sms_code("flow", "1234")
        self.assertEqual(caught.exception.error_code, "WEBVPN_FLOW_EXPIRED")
        self.assertIsNone(client._webvpn_sms_flow)

    def test_expired_webvpn_session_silently_reauthenticates_with_password(self):
        client = NEUAuthClient("20250001", "secret", network_mode="webvpn")
        with (
            patch.object(client, "_webvpn_health_check", return_value=False),
            patch.object(
                client,
                "start_webvpn_password_login",
                return_value={
                    "status": "authenticated",
                    "username": "20250001",
                },
            ) as password_login,
        ):
            self.assertTrue(client.ensure_login())

        password_login.assert_called_once_with()
        self.assertTrue(client.is_logged_in)

    def test_silent_webvpn_reauthentication_preserves_foreground_sms_flow(self):
        client = NEUAuthClient("20250001", "secret", network_mode="webvpn")

        def require_sms():
            client._webvpn_sms_flow = {"id": "silent-flow"}
            return {
                "status": "sms_required",
                "flow_id": "silent-flow",
                "expires_in": 180,
            }

        with (
            patch.object(client, "_webvpn_health_check", return_value=False),
            patch.object(
                client,
                "start_webvpn_password_login",
                side_effect=require_sms,
            ),
        ):
            self.assertFalse(client.ensure_login())

        self.assertEqual(client._webvpn_sms_flow["id"], "silent-flow")
        self.assertFalse(client.is_logged_in)

    def test_pending_sms_flow_blocks_repeated_background_password_login(self):
        client = NEUAuthClient("20250001", "secret", network_mode="webvpn")
        client._webvpn_sms_flow = {
            "id": "pending-flow",
            "expires_at": 4102444800,
        }

        with (
            patch.object(client, "_webvpn_health_check") as health_check,
            patch.object(client, "start_webvpn_password_login") as password_login,
        ):
            self.assertFalse(client.ensure_login())
            self.assertFalse(client.ensure_login())

        health_check.assert_not_called()
        password_login.assert_not_called()
        self.assertEqual(client._webvpn_sms_flow["id"], "pending-flow")


if __name__ == "__main__":
    unittest.main()
