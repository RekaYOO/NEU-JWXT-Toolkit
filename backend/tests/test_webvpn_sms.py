import unittest
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


if __name__ == "__main__":
    unittest.main()
