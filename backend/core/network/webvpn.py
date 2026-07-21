"""NEU WebVPN URL conversion.

The gateway uses AES-CFB encrypted host names.  Keep this implementation
isolated so a future gateway rule change has one update point.
"""

import re
from urllib.parse import urlsplit, urlunsplit

from Crypto.Cipher import AES


WEBVPN_ORIGIN = "https://webvpn.neu.edu.cn"
WEBVPN_ENTRY_URL = f"{WEBVPN_ORIGIN}/login?cas_login=true"
URL_PREFIX = "62304135386136393339346365373340"
KEY_TEXT = b"b0A58a69394ce73@"


class WebVPNUrlCodec:
    """Convert an ordinary HTTP(S) URL into the NEU WebVPN form."""

    _EXPLICIT_PORT = re.compile(r"^https?://(?:\[[^]]+\]|[^/:]+):(\d+)(?:/|$)", re.I)

    @staticmethod
    def is_webvpn_url(url: str) -> bool:
        try:
            return urlsplit(url).hostname == "webvpn.neu.edu.cn"
        except ValueError:
            return False

    @staticmethod
    def encrypt_hostname(hostname: str) -> str:
        cipher = AES.new(KEY_TEXT, AES.MODE_CFB, iv=KEY_TEXT, segment_size=128)
        return cipher.encrypt(hostname.encode("utf-8")).hex()

    @classmethod
    def convert_url(cls, url: str) -> str:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("WebVPN 仅支持完整的 HTTP/HTTPS URL")
        if cls.is_webvpn_url(url):
            return url

        port_match = cls._EXPLICIT_PORT.match(url)
        protocol_path = parsed.scheme
        if port_match:
            protocol_path = f"{protocol_path}-{port_match.group(1)}"

        encrypted_host = cls.encrypt_hostname(parsed.hostname)
        path = parsed.path or ""
        vpn_path = f"/{protocol_path}/{URL_PREFIX}{encrypted_host}{path}"
        return urlunsplit(("https", "webvpn.neu.edu.cn", vpn_path, parsed.query, parsed.fragment))
