import unittest

import requests
from unittest.mock import Mock
from requests.cookies import create_cookie

from backend.core.auth.client import (
    NEUAuthClient,
    WEBVPN_ERR_CAMPUS_NETWORK,
    WebVPNLoginError,
)
from backend.core.network.webvpn import WebVPNUrlCodec
from backend.core.auth.session_manager import AuthSessionManager, is_remote_read_context


class WebVPNUrlCodecTests(unittest.TestCase):
    def test_identifies_only_the_official_campus_network_block_page(self):
        response = Mock(status_code=403, url="https://webvpn.neu.edu.cn/")
        response.text = "<title>访问被拒绝</title>WebVPN仅用于我校师生在校外登录，校园网用户无需使用WebVPN"
        self.assertTrue(NEUAuthClient._is_webvpn_campus_block_response(response))

        ordinary = Mock(status_code=403, url="https://webvpn.neu.edu.cn/tpass/login")
        ordinary.text = "Forbidden"
        self.assertFalse(NEUAuthClient._is_webvpn_campus_block_response(ordinary))

    def test_campus_block_has_stable_error_code_and_message(self):
        response = Mock(status_code=403, url="https://webvpn.neu.edu.cn/")
        response.text = "访问被拒绝：校园网用户无需使用WebVPN"
        client = NEUAuthClient(network_mode="webvpn", restore_session=False)
        with self.assertRaises(WebVPNLoginError) as caught:
            client._raise_for_webvpn_response(response)
        self.assertEqual(caught.exception.error_code, WEBVPN_ERR_CAMPUS_NETWORK)
        self.assertIn("校内直连", str(caught.exception))

    def test_ordinary_webvpn_403_is_not_misclassified(self):
        response = Mock(status_code=403, url="https://webvpn.neu.edu.cn/tpass/login")
        response.text = "Forbidden"
        response.raise_for_status.side_effect = requests.HTTPError("403 Client Error")
        client = NEUAuthClient(network_mode="webvpn", restore_session=False)

        with self.assertRaises(requests.HTTPError):
            client._raise_for_webvpn_response(response)
        self.assertNotEqual(
            getattr(client, "_last_webvpn_error_code", ""),
            WEBVPN_ERR_CAMPUS_NETWORK,
        )

    def test_session_request_surfaces_campus_block_without_retrying(self):
        response = Mock(
            status_code=403,
            url="https://webvpn.neu.edu.cn/",
            text="访问被拒绝：WebVPN仅用于我校师生在校外登录，校园网用户无需使用WebVPN",
        )
        session = Mock()
        session.request.return_value = response
        client = NEUAuthClient(network_mode="webvpn", restore_session=False)
        client._session = session

        with self.assertRaises(WebVPNLoginError) as caught:
            client._session_request("GET", "https://webvpn.neu.edu.cn/")
        self.assertEqual(caught.exception.error_code, WEBVPN_ERR_CAMPUS_NETWORK)
        session.request.assert_called_once()

    def test_shared_read_uses_isolated_cookie_snapshot_and_does_not_roll_back_auth(self):
        client = NEUAuthClient(restore_session=False)
        client._session.cookies.set_cookie(
            create_cookie(name="SESSION", value="new", domain="jwxt.neu.edu.cn", path="/")
        )
        isolated, snapshot = client._isolated_read_session()
        self.assertEqual(isolated.cookies.get("SESSION"), "new")

        # Authentication/mutation advances the primary cookie while the read
        # is in flight.  The stale read response must not overwrite it.
        client._session.cookies.set("SESSION", "newer", domain="jwxt.neu.edu.cn", path="/")
        isolated.cookies.set("SESSION", "stale", domain="jwxt.neu.edu.cn", path="/")
        client._merge_isolated_read_cookies(isolated, snapshot)
        self.assertEqual(client._session.cookies.get("SESSION"), "newer")

    def test_shared_read_context_is_reported_only_inside_read_guard(self):
        manager = AuthSessionManager()
        self.assertFalse(is_remote_read_context())
        with manager.remote_read_guard():
            self.assertTrue(is_remote_read_context())
        self.assertFalse(is_remote_read_context())
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
