import unittest

from backend.core.network.webvpn import WebVPNUrlCodec


class WebVPNUrlCodecTests(unittest.TestCase):
    def test_converts_jwxt_url_like_browser_extension(self):
        self.assertEqual(
            WebVPNUrlCodec.convert_url(
                "https://jwxt.neu.edu.cn/jwapp/sys/homeapp/api/home/currentUser.do"
            ),
            "https://webvpn.neu.edu.cn/https/"
            "62304135386136393339346365373340baf6bc2bc4cb43c8bc1d6f66c806db"
            "/jwapp/sys/homeapp/api/home/currentUser.do",
        )

    def test_preserves_explicit_port_query_and_fragment(self):
        self.assertEqual(
            WebVPNUrlCodec.convert_url("http://zljk.neu.edu.cn:8080/api/test?a=1#anchor"),
            "https://webvpn.neu.edu.cn/http-8080/"
            "62304135386136393339346365373340aaedae34c4cb43c8bc1d6f66c806db"
            "/api/test?a=1#anchor",
        )

    def test_keeps_existing_webvpn_url(self):
        url = "https://webvpn.neu.edu.cn/login?cas_login=true"
        self.assertEqual(WebVPNUrlCodec.convert_url(url), url)


if __name__ == "__main__":
    unittest.main()
