import unittest
from unittest.mock import Mock, patch

from backend.core.auth.client import NEUAuthClient, WebVPNLoginError


class WebVPNCaptchaTests(unittest.TestCase):
    def test_extracts_second_auth_form_without_trusting_preview_image(self):
        html = """
        <form id="second_auth_form" action="/tpass/secondAuth">
          <input type="hidden" name="RelayState" value="relay">
          <input type="hidden" name="execution" value="exec">
          <input name="method" value="mobile">
          <img src="/static/images/captcha-preview.jpg">
        </form>
        """
        result = NEUAuthClient._extract_second_auth_form(
            html, "https://webvpn.neu.edu.cn/https/token/tpass/login"
        )
        self.assertEqual(result["form_action"], "https://webvpn.neu.edu.cn/tpass/secondAuth")
        self.assertNotIn("captcha_url", result)
        self.assertEqual(result["hidden_fields"]["execution"], "exec")

    def test_builds_real_webvpn_captcha_url_with_fresh_random_query(self):
        page_url = "https://webvpn.neu.edu.cn/https/token/tpass/login?service=test"
        with patch("backend.core.auth.client.random.random", return_value=0.8967832515796772):
            result = NEUAuthClient._build_webvpn_captcha_url(page_url)
        self.assertEqual(
            result,
            "https://webvpn.neu.edu.cn/https/token/tpass/code?vpn-1&0.8967832515796772",
        )

    def test_rejects_captcha_url_outside_webvpn_tpass(self):
        with self.assertRaises(WebVPNLoginError):
            NEUAuthClient._build_webvpn_captcha_url("https://example.com/tpass/login")

    def test_fetches_jpeg_with_referer_and_preserves_media_type(self):
        page_url = "https://webvpn.neu.edu.cn/https/token/tpass/login?service=test"
        response = Mock()
        response.url = "https://webvpn.neu.edu.cn/https/token/tpass/code?vpn-1&0.1250000000000000"
        response.content = b"\xff\xd8\xffcaptcha"
        response.headers = {"Content-Type": "image/jpeg; charset=binary"}
        response.raise_for_status.return_value = None
        client = NEUAuthClient("20250001", "secret", network_mode="webvpn")
        client._session = Mock()
        client.session.get.return_value = response
        flow = {"page_url": page_url}

        with patch("backend.core.auth.client.random.random", return_value=0.125):
            result = client._fetch_webvpn_captcha(flow)

        self.assertTrue(result["captcha_image"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(flow["captcha_media_type"], "image/jpeg")
        self.assertEqual(flow["captcha_code"], "")
        request = client.session.get.call_args
        self.assertEqual(
            request.args[0],
            "https://webvpn.neu.edu.cn/https/token/tpass/code?vpn-1&0.1250000000000000",
        )
        self.assertEqual(request.kwargs["headers"]["Referer"], page_url)

    def test_rejects_captcha_response_redirected_back_to_login(self):
        page_url = "https://webvpn.neu.edu.cn/https/token/tpass/login?service=test"
        response = Mock()
        response.url = page_url
        response.content = b"\xff\xd8\xffcaptcha"
        response.headers = {"Content-Type": "image/jpeg"}
        response.raise_for_status.return_value = None
        client = NEUAuthClient("20250001", "secret", network_mode="webvpn")
        client._session = Mock()
        client.session.get.return_value = response

        with self.assertRaisesRegex(WebVPNLoginError, "重定向"):
            client._fetch_webvpn_captcha({"page_url": page_url})

    def test_rejects_non_image_or_empty_captcha_response(self):
        client = NEUAuthClient("20250001", "secret", network_mode="webvpn")
        for content_type, content in (("text/html", b"login"), ("image/jpeg", b"")):
            response = Mock()
            response.headers = {"Content-Type": content_type}
            response.content = content
            with self.subTest(content_type=content_type, content=content):
                with self.assertRaises(WebVPNLoginError):
                    client._webvpn_captcha_media_type(response)

    def test_corrects_gateway_mime_when_jpeg_header_contains_gif_bytes(self):
        response = Mock()
        response.headers = {"Content-Type": "image/jpeg"}
        response.content = b"GIF89a" + b"captcha"
        self.assertEqual(NEUAuthClient._webvpn_captcha_media_type(response), "image/gif")


if __name__ == "__main__":
    unittest.main()
