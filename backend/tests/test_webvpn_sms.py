import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.core.auth.client import NEUAuthClient


class WebVPNSMSLoginTests(unittest.TestCase):
    def _response(self, url, text="", json_data=None):
        response = Mock()
        response.url = url
        response.text = text
        response.raise_for_status.return_value = None
        response.json.return_value = json_data or {}
        return response

    def test_sms_flow_uses_official_device_requests_then_resubmits_form(self):
        login_url = "https://webvpn.neu.edu.cn/https/token/tpass/login?service=test"
        login_page = self._response(
            login_url,
            '<form id="loginForm" action=""><input type="hidden" name="lt" value="ticket"></form>',
        )
        sms_page = self._response(login_url, "<script>phone('murmur', 'details')</script>")
        sms_sent = self._response(login_url, json_data={"info": "send"})
        sms_verified = self._response(login_url, json_data={"info": "ok"})
        completed = self._response("https://webvpn.neu.edu.cn/login")

        client = NEUAuthClient("20250001", "secret", network_mode="webvpn")
        client._session = Mock()
        client.session.get.return_value = login_page
        client.session.post.side_effect = [sms_page, sms_sent, sms_verified, completed]

        with (
            patch.object(client, "_webvpn_health_check", return_value=True),
            patch.object(client, "_sync_cas_cookie_to_webvpn"),
        ):
            started = client.start_webvpn_password_login()
            self.assertEqual(started["status"], "sms_required")
            self.assertEqual(client.send_webvpn_sms_code(started["flow_id"])["status"], "sent")
            completed_result = client.verify_webvpn_sms_code(started["flow_id"], "123456")

        self.assertEqual(completed_result["status"], "authenticated")
        post_calls = client.session.post.call_args_list
        self.assertEqual(post_calls[1].kwargs["data"], {"m": "2"})
        self.assertEqual(post_calls[2].kwargs["data"]["m"], "3")
        self.assertEqual(post_calls[2].kwargs["data"]["d"], "murmur")
        self.assertEqual(post_calls[2].kwargs["data"]["i"], "details")
        self.assertEqual(post_calls[3].kwargs["data"]["un"], "20250001")

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

    def test_silent_webvpn_reauthentication_never_leaves_an_sms_flow(self):
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

        self.assertIsNone(client._webvpn_sms_flow)
        self.assertFalse(client.is_logged_in)


if __name__ == "__main__":
    unittest.main()
