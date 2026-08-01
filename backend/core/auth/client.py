"""
neu_auth/client.py
==================
东北大学统一身份认证（CAS）登录客户端

特性：
- HTTP/HTTPS 协议自动回退（目标服务协议切换时自动适配）
- 动态密钥刷新（登录时从页面提取最新公钥，失败时自动从服务器获取）
- 自动重试机制
- 票据失效自动重新登录
- CAS Cookie 持久化（免密刷新票据）
- 请求限流保护
"""

import base64
import json
import os
import re
import time
import logging
import uuid
from functools import wraps
from typing import Optional, Callable, Dict, Any
from urllib.parse import urlencode, urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

from backend.core.network import WEBVPN_ENTRY_URL, WEBVPN_ORIGIN, WebVPNUrlCodec

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────

CAS_BASE_URL = "https://pass.neu.edu.cn/tpass"
CAS_LOGIN_URL = f"{CAS_BASE_URL}/login"

# CAS 登录 JS 资源 URL（包含最新 RSA 公钥，每次从服务器拉取以保证最新）
_LOGIN_JS_URL = f"{CAS_BASE_URL}/comm/neu/js/login_neu.js"

# 内置默认 RSA 公钥（与服务器当前版本一致，fallback 使用）
_RSA_PUBLIC_KEY_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAnjA28DLKXZzxbKmo9/1W"
    "kVLf1mr+wtLXLXt6sC4WiBCtsbzF5ewm7ARZeAdS3iZtqlYPn6IcUoOw42H8nAK/"
    "tfFcIb6dZ1K0atn0U39oWCGPzYuKtLJeMuNZiDXVuAXtojrckOjLW9B3gUnaNGLu"
    "Ix0fYe66l0o9WjU2cGLNZQfiIxs2h00z1EA9IdSnVxiVQWSD+lsP3JZXh2TT287l"
    "a4Y4603SQNKTK/QvXfcmccwTEd1IW6HwGxD6QrkInBiHisKWxmveN7UDSaQRZ/J9"
    "7G0YC32pD38WT53izXeK0p/kU/X37VP555um1wVWFvPIuc9I7gMP1+hq5a+X6c++"
    "tQIDAQAB"
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

MAX_RETRIES = 3
RETRY_DELAY = 2  # 秒

# 登录错误类型
LOGIN_ERR_WRONG_PWD = "WRONG_PASSWORD"   # 密码错误
LOGIN_ERR_BAD_KEY = "BAD_KEY"             # 公钥/加密错误
LOGIN_ERR_UNKNOWN = "UNKNOWN"             # 未知错误


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def retry_on_error(max_retries: int = MAX_RETRIES, delay: float = RETRY_DELAY):
    """装饰器：请求失败时自动重试"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except requests.RequestException as e:
                    last_exception = e
                    logger.warning(
                        "请求失败 (尝试 %s/%s): %s",
                        attempt + 1,
                        max_retries,
                        type(e).__name__,
                    )
                    if attempt < max_retries - 1:
                        time.sleep(delay * (attempt + 1))  # 指数退避
            raise last_exception
        return wrapper
    return decorator


def _rsa_encrypt(username: str, password: str) -> str:
    """RSA加密（使用内置默认公钥）"""
    der = base64.b64decode(_RSA_PUBLIC_KEY_B64)
    key = RSA.import_key(der)
    cipher = PKCS1_v1_5.new(key)
    plaintext = (username + password).encode("utf-8")
    encrypted = cipher.encrypt(plaintext)
    return base64.b64encode(encrypted).decode("utf-8")


def _rsa_encrypt_with_key(username: str, password: str, key_b64: str) -> str:
    """
    RSA加密（使用指定公钥）
    
    Args:
        username: 学号
        password: 密码
        key_b64: Base64 编码的 RSA 公钥（PKCS#8/PKCS#1 DER 格式）
        
    Returns:
        Base64 编码的加密结果
    """
    der = base64.b64decode(key_b64)
    key = RSA.import_key(der)
    cipher = PKCS1_v1_5.new(key)
    plaintext = (username + password).encode("utf-8")
    encrypted = cipher.encrypt(plaintext)
    return base64.b64encode(encrypted).decode("utf-8")


def _fetch_rsa_key_from_server(timeout: int = 10) -> Optional[str]:
    """
    从 CAS 服务器动态获取最新的 RSA 公钥
    
    公钥嵌在 login_neu.js 中，格式为：
        const publicKeyStr = "MIIBIjANBg...";
    
    每次请求强制绕过缓存（Cache-Control: no-cache + query ts），
    确保拿到服务端最新版本。
    
    Returns:
        公钥 Base64 字符串，获取失败返回 None
    """
    try:
        resp = requests.get(
            _LOGIN_JS_URL,
            params={"ts": str(int(time.time()))},   # 时间戳绕过 CDN 缓存
            headers={
                "Cache-Control": "no-cache",
                "User-Agent": DEFAULT_HEADERS["User-Agent"],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        
        # 提取: const publicKeyStr = "MIIBIjANBg...";
        match = re.search(
            r'const\s+publicKeyStr\s*=\s*"([A-Za-z0-9+/=]+)"',
            resp.text
        )
        if match:
            key = match.group(1)
            logger.debug(f"从服务器获取到新公钥，长度: {len(key)}")
            return key
        
        logger.warning("未能从 login_neu.js 中提取到公钥")
        return None
        
    except requests.RequestException as e:
        logger.warning("从服务器获取公钥失败（网络错误）: %s", type(e).__name__)
        return None
    except Exception as e:
        logger.warning("从服务器获取公钥失败: %s", type(e).__name__)
        return None


def _is_key_error(error_msg: str) -> bool:
    """
    判断登录错误是否可能由公钥问题引起
    
    当服务端公钥轮换后，旧公钥加密的密文会导致解密失败，
    错误页面通常包含相关提示词。
    """
    if not error_msg:
        return False
    msg = error_msg.lower()
    key_error_keywords = [
        "crypto", "rsa", "encrypt", "decrypt",
        "解密", "加密", "密文", "illegal", "bad",
        "parameter", "padding", "cipher",
        "服务异常", "系统异常", "操作异常",
    ]
    return any(kw in msg for kw in key_error_keywords)


def _classify_login_error(error_msg: str) -> str:
    """
    对登录错误进行分类，用于判断是否需要触发密钥刷新
    
    Returns:
        LOGIN_ERR_WRONG_PWD  - 密码/账号错误，不需要刷新公钥
        LOGIN_ERR_BAD_KEY   - 公钥/加密错误，需要刷新公钥重试
        LOGIN_ERR_UNKNOWN   - 无法确定
    """
    if not error_msg:
        return LOGIN_ERR_UNKNOWN
    msg = error_msg.lower()
    
    # 明确是密码/账号错误
    pwd_keywords = [
        "密码", "password", "wrong", "incorrect",
        "账号", "用户名", "不存在", "学号",
        "登录失败", "认证失败",
    ]
    if any(kw in msg for kw in pwd_keywords):
        # 排除同时含有关键词的情况（优先判定为密钥问题）
        if not any(kw in msg for kw in ["crypto", "rsa", "encrypt", "decrypt", "解密", "加密", "密文", "illegal"]):
            return LOGIN_ERR_WRONG_PWD
    
    # 公钥/加密相关
    if _is_key_error(error_msg):
        return LOGIN_ERR_BAD_KEY
    
    return LOGIN_ERR_UNKNOWN


# ── 主客户端 ──────────────────────────────────────────────────────────────────

class NEULoginError(Exception):
    """
    登录失败异常
    
    Attributes:
        error_type: 错误类型
            - WRONG_PASSWORD: 密码错误
            - BAD_KEY:        公钥/加密错误
            - UNKNOWN:         未知错误
    """
    def __init__(self, message: str, error_type: str = LOGIN_ERR_UNKNOWN):
        super().__init__(message)
        self.error_type = error_type


class WebVPNRequiredError(NEULoginError):
    """Direct campus access is unavailable and WebVPN authentication is needed."""


class WebVPNLoginError(NEULoginError):
    """The WebVPN QR login flow could not be completed."""


class DirectAccessError(NEULoginError):
    """A direct-campus request could not reach the academic system."""


class NEUAuthClient:
    """
    东北大学统一身份认证登录客户端
    
    使用示例：
        >>> client = NEUAuthClient("学号", "密码")
        >>> client.login()
        >>> scores = client.academic.get_scores()
    """

    def __init__(
        self,
        username: str = "",
        password: str = "",
        timeout: int = 15,
        verify_ssl: bool = True,
        cookie_file: Optional[str] = None,
        network_mode: str = "direct",
        restore_session: bool = True,
    ):
        self.username = username
        self.password = password
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.target = "https://jwxt.neu.edu.cn"
        self.cookie_file = cookie_file  # Cookie 持久化文件路径
        if network_mode not in {"direct", "webvpn"}:
            raise ValueError("network_mode 必须为 direct 或 webvpn")
        self.network_mode = network_mode
        self.active_mode = "webvpn" if network_mode == "webvpn" else "direct"
        
        # 当前使用的 RSA 公钥（每次登录时从页面动态更新）
        self._current_key: Optional[str] = None
        
        # 已知可用的协议（https:// 或 http://），用于 jwxt.neu.edu.cn 请求的协议回退
        self._protocol_override: Optional[str] = None

        self._session = requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)
        self._logged_in = False
        self._academic = None
        self._academic_report = None  # 学业监测报告 API
        self._evaluation = None       # 教学质量评价系统 API
        self._webvpn_qr_flow: Optional[Dict[str, Any]] = None
        self._webvpn_sms_flow: Optional[Dict[str, Any]] = None
        
        # 自动恢复入口可以读取历史会话；用户主动登录必须从干净会话开始，
        # 避免旧 WebVPN Cookie 将新登录导向错误页面。
        if cookie_file and restore_session:
            self._load_cookies()

    def login(self, target: str = "https://jwxt.neu.edu.cn") -> bool:
        """
        执行 CAS 登录（含 HTTP/HTTPS 协议回退）
        
        登录策略：
        1. 优先使用目标 URL 的协议尝试登录
        2. 若连接失败（ConnectionError/SSLError/重定向循环），自动切换协议重试
        3. 核心登录流程：优先从登录页面提取最新 RSA 公钥，失败时从 JS 文件刷新
        
        Args:
            target: 目标系统 URL
            
        Returns:
            登录是否成功
        """
        self.target = target
        if self.active_mode == "webvpn":
            raise WebVPNRequiredError("当前为 WebVPN 模式，请使用微信扫码或短信验证码登录")
        try:
            return self._do_login(target)
        except WebVPNRequiredError:
            self.active_mode = "webvpn"
            raise
        except NEULoginError as e:
            if "网络错误" in str(e):
                raise DirectAccessError("直连教务系统失败，请检查校园网络；校外请切换 WebVPN 模式") from e
            raise
        except (requests.exceptions.ConnectionError, requests.exceptions.SSLError,
                requests.exceptions.Timeout, requests.exceptions.TooManyRedirects) as e:
            raise DirectAccessError("直连教务系统超时，请检查校园网络；校外请切换 WebVPN 模式") from e

    @retry_on_error(max_retries=3, delay=2)
    def _do_login(self, target: str) -> bool:
        """
        CAS 登录核心逻辑
        
        流程：
        1. 获取登录页 → 从 HTML 提取最新 RSA 公钥
        2. 使用提取/缓存的公钥尝试登录
        3. 若失败且非密码错误 → 从 JS 文件刷新公钥重试
        4. 若仍失败 → 抛出 NEULoginError
        
        Args:
            target: 目标系统 URL
            
        Returns:
            登录是否成功
        """
        service_url = self._resolve_service_url(target)
        logger.info("开始 CAS 登录...")

        # Step 1: 获取登录页（含 lt 等隐藏字段）
        login_page_url = f"{CAS_LOGIN_URL}?service={requests.utils.quote(service_url, safe='')}"
        resp = self._session.get(
            login_page_url,
            timeout=self.timeout,
            verify=self.verify_ssl,
            allow_redirects=True,
        )
        resp.raise_for_status()

        if WebVPNUrlCodec.is_webvpn_url(resp.url):
            self.active_mode = "webvpn"
            raise WebVPNRequiredError("教务系统已跳转到 WebVPN 登录页")

        # 如果已登录（直接跳转到目标系统）
        if urlparse(resp.url).netloc != urlparse(CAS_LOGIN_URL).netloc:
            logger.info("已有有效会话")
            self._logged_in = True
            self._save_cookies()
            return True

        hidden = self._extract_hidden_fields(resp.text)

        # Step 1.5: 尝试从登录页 HTML 提取最新 RSA 公钥
        html_key = self._extract_rsa_key_from_html(resp.text)
        if html_key and html_key != (self._current_key or _RSA_PUBLIC_KEY_B64):
            logger.info("从登录页面提取到新 RSA 公钥，将优先使用")
            self._current_key = html_key

        # Step 2: 首次尝试登录（使用当前/提取的公钥）
        key_to_use = self._current_key or _RSA_PUBLIC_KEY_B64
        error_msg = self._do_login_submit(hidden, service_url, key_to_use)
        
        if error_msg is None:
            self._logged_in = True
            self._current_key = key_to_use  # 记录成功的密钥
            self._save_cookies()
            return True

        # Step 3: 分析错误，非密码错误时尝试刷新公钥
        error_type = _classify_login_error(error_msg)
        logger.warning("首次登录失败，错误类型: %s", error_type)

        if error_type != LOGIN_ERR_WRONG_PWD:
            # 非密码错误 → 尝试从服务器 JS 文件获取最新公钥
            new_key = _fetch_rsa_key_from_server(self.timeout)
            if new_key and new_key != key_to_use:
                logger.info("检测到服务器公钥已更新，清除旧 Cookie，重新尝试登录...")
                self.clear_cookies()
                # 重新获取登录页（Cookie 已清除）
                resp = self._session.get(
                    login_page_url,
                    timeout=self.timeout,
                    verify=self.verify_ssl,
                    allow_redirects=True,
                )
                resp.raise_for_status()
                hidden = self._extract_hidden_fields(resp.text)
                error_msg = self._do_login_submit(hidden, service_url, new_key)
                if error_msg is None:
                    self._logged_in = True
                    self._current_key = new_key
                    self._save_cookies()
                    return True
                logger.warning(
                    "使用新公钥重试仍然失败，错误类型: %s",
                    _classify_login_error(error_msg),
                )
            elif new_key is None:
                logger.warning("无法从服务器获取新公钥（网络问题）")

        # 所有重试均失败
        raise NEULoginError(f"登录失败: {error_msg}", error_type=error_type)

    def _do_login_submit(
        self,
        hidden: dict,
        service_url: str,
        key_b64: str,
    ) -> Optional[str]:
        """
        执行登录表单提交
        
        Args:
            hidden: 从登录页提取的隐藏字段（lt 等）
            service_url: CAS service URL
            key_b64: 本次使用的 RSA 公钥
            
        Returns:
            None 表示登录成功，
            str 表示错误信息
        """
        try:
            post_url = f"{CAS_LOGIN_URL}?service={requests.utils.quote(service_url, safe='')}"
            resp2 = self._submit_login_form(hidden, key_b64, post_url)
            resp2.raise_for_status()

            # 判断是否仍在 CAS 登录页（登录失败）
            final_url = resp2.url
            if WebVPNUrlCodec.is_webvpn_url(final_url):
                self.active_mode = "webvpn"
                raise WebVPNRequiredError("教务系统已跳转到 WebVPN 登录页")
            if urlparse(final_url).netloc == urlparse(CAS_LOGIN_URL).netloc:
                return self._extract_error_message(resp2.text)
            
            return None  # 登录成功
            
        except requests.RequestException as e:
            return f"网络错误: {e}"

    def _build_login_form(self, hidden: Dict[str, str], key_b64: str) -> Dict[str, str]:
        """Build the same credentials payload used by the official CAS form."""
        rsa_encrypted = _rsa_encrypt_with_key(self.username, self.password, key_b64)
        return {
            "un": self.username,
            "pd": self.password,
            "rsa": rsa_encrypted,
            "ul": str(len(self.username)),
            "pl": str(len(self.password)),
            "lt": hidden.get("lt", ""),
            "execution": hidden.get("execution", "e1s1"),
            "_eventId": "submit",
        }

    def _submit_login_form(
        self,
        hidden: Dict[str, str],
        key_b64: str,
        post_url: str,
        form_data: Optional[Dict[str, str]] = None,
    ) -> requests.Response:
        """Submit a direct or WebVPN-proxied CAS password form."""
        return self._session.post(
            post_url,
            data=form_data or self._build_login_form(hidden, key_b64),
            timeout=self.timeout,
            verify=self.verify_ssl,
            allow_redirects=True,
        )

    # ── WebVPN QR 登录 ─────────────────────────────────────────────────────────

    @staticmethod
    def _is_webvpn_login_url(url: str) -> bool:
        parsed = urlparse(url)
        return (
            parsed.hostname == "webvpn.neu.edu.cn"
            and "/tpass/login" in parsed.path
        )

    @staticmethod
    def _safe_url_metadata(url: str) -> Dict[str, Any]:
        """Return redirect diagnostics without retaining tickets or query data."""
        parsed = urlparse(url)
        return {
            "host": parsed.hostname or "",
            "path": parsed.path or "/",
            "query_keys": sorted(parse_qs(parsed.query, keep_blank_values=True).keys()),
        }

    @staticmethod
    def _safe_set_cookie_names(response: requests.Response) -> list[str]:
        raw_headers = getattr(response.raw, "headers", None)
        if raw_headers and hasattr(raw_headers, "getlist"):
            return [item.split("=", 1)[0] for item in raw_headers.getlist("Set-Cookie")]
        header = response.headers.get("Set-Cookie", "")
        return [header.split("=", 1)[0]] if header else []

    def _safe_cookie_metadata(self) -> list[Dict[str, str]]:
        return [
            {"name": cookie.name, "domain": cookie.domain}
            for cookie in self._session.cookies
            if cookie.domain.lstrip(".").endswith("neu.edu.cn")
        ]

    def start_webvpn_qr_login(self, expires_in: int = 180) -> Dict[str, Any]:
        """Create a QR login flow bound to this client's requests session."""
        expires_in = max(60, min(int(expires_in), 600))
        self.active_mode = "webvpn"
        service = WEBVPN_ENTRY_URL
        direct_login_url = f"{CAS_LOGIN_URL}?service={requests.utils.quote(service, safe='')}"
        response = self._session.get(
            direct_login_url,
            timeout=self.timeout,
            verify=self.verify_ssl,
            allow_redirects=True,
        )
        response.raise_for_status()

        parsed_login_url = urlparse(response.url)
        if parsed_login_url.hostname != "pass.neu.edu.cn" or "/tpass/login" not in parsed_login_url.path:
            raise WebVPNLoginError("WebVPN 统一认证页面未处于二维码登录状态")

        qr_uuid = str(uuid.uuid4())
        # The official QR payload uses a direct CAS URL and keeps service
        # verbatim. Proxying this URL only opens WebVPN in the scanner.
        qr_content = f"{CAS_BASE_URL}/qyQrLogin?uuid={qr_uuid}&service={service}"
        self._webvpn_qr_flow = {
            "id": str(uuid.uuid4()),
            "uuid": qr_uuid,
            "qr_status_url": f"{CAS_BASE_URL}/checkQRCodeScan",
            "login_page_url": direct_login_url,
            "expires_at": time.time() + expires_in,
        }
        return {
            "flow_id": self._webvpn_qr_flow["id"],
            "qr_content": qr_content,
            "expires_in": expires_in,
            "poll_interval": 3,
        }

    def poll_webvpn_qr_login(self, flow_id: str) -> Dict[str, Any]:
        """Poll the real CAS QR endpoint and finalize the same HTTP session."""
        flow = self._webvpn_qr_flow
        if not flow or flow["id"] != flow_id:
            raise WebVPNLoginError("二维码登录流程不存在或已被替换")
        if time.time() >= flow["expires_at"]:
            self._webvpn_qr_flow = None
            return {"status": "expired"}

        status_url = (
            f"{flow['qr_status_url']}?"
            f"{urlencode({'random': time.time(), 'uuid': flow['uuid']})}"
        )
        response = self._session.get(
            status_url,
            timeout=self.timeout,
            verify=self.verify_ssl,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": flow["login_page_url"],
            },
        )
        response.raise_for_status()
        if not response.text.strip():
            # The production endpoint deliberately returns an empty body while
            # the QR code has not been scanned yet.
            return {"status": "pending"}
        try:
            result = response.json()
        except ValueError as exc:
            raise WebVPNLoginError("二维码状态接口返回了非 JSON 响应") from exc

        redirect_url = result.get("redirect_url")
        if not redirect_url:
            return {"status": "pending"}

        callback_url = urljoin(flow["login_page_url"], redirect_url)
        flow["callback"] = self._safe_url_metadata(callback_url)
        flow["cookies_after_poll"] = self._safe_cookie_metadata()
        flow["poll_set_cookies"] = self._safe_set_cookie_names(response)
        callback_host = urlparse(callback_url).hostname
        if callback_host not in {"pass.neu.edu.cn", "webvpn.neu.edu.cn"}:
            raise WebVPNLoginError("二维码登录返回了不受信任的跳转地址")

        # Match the official page: follow the CAS redirect first, then let its
        # service ticket establish the WebVPN session.  Converting a pass URL
        # before this request breaks the ticket callback chain.
        completion = self._session.get(
            callback_url,
            timeout=self.timeout,
            verify=self.verify_ssl,
            allow_redirects=True,
        )
        completion.raise_for_status()
        flow["completion"] = {
            "status_code": completion.status_code,
            "final": self._safe_url_metadata(completion.url),
            "history": [
                {
                    "status_code": item.status_code,
                    "location": self._safe_url_metadata(item.headers.get("Location", "")),
                }
                for item in completion.history
            ],
            "cookies": self._safe_cookie_metadata(),
            "set_cookies": self._safe_set_cookie_names(completion),
        }
        logger.info("WebVPN QR callback diagnostics: %s", flow["completion"])
        self._sync_cas_cookie_to_webvpn(flow)

        # WebVPN may leave the browser on its proxied CAS page even after it
        # has issued the gateway ticket cookie.  The actual success criterion
        # is whether that cookie can establish the target JWXT session.
        if not self._webvpn_health_check(flow):
            raise WebVPNLoginError("扫码已完成，但未能建立教务系统会话")

        self._logged_in = True
        self._webvpn_qr_flow = None
        self._save_cookies()
        return {"status": "authenticated", "username": self.username or None}

    def cancel_webvpn_qr_login(self, flow_id: Optional[str] = None) -> None:
        if self._webvpn_qr_flow and (flow_id is None or self._webvpn_qr_flow["id"] == flow_id):
            self._webvpn_qr_flow = None

    # ── WebVPN password and SMS login ────────────────────────────────────────

    @staticmethod
    def _extract_login_form_action(html: str, page_url: str) -> str:
        """Resolve the real CAS form action, including a WebVPN proxy prefix."""
        soup = BeautifulSoup(html, "lxml")
        form = soup.select_one("form#loginForm") or soup.select_one("form[action]")
        action = form.get("action", "") if form else ""
        return urljoin(page_url, action or page_url)

    @staticmethod
    def _extract_phone_challenge(html: str) -> Optional[tuple[str, str]]:
        """Extract the server-issued values passed to the official phone() handler."""
        match = re.search(
            r"phone\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)",
            html,
        )
        return (match.group(1), match.group(2)) if match else None

    def _get_webvpn_sms_flow(self, flow_id: str) -> Dict[str, Any]:
        flow = self._webvpn_sms_flow
        if not flow or flow["id"] != flow_id:
            raise WebVPNLoginError("短信验证流程不存在或已被替换")
        if time.time() >= flow["expires_at"]:
            self._webvpn_sms_flow = None
            raise WebVPNLoginError("短信验证码登录已过期，请重新输入账号密码")
        return flow

    def start_webvpn_password_login(self) -> Dict[str, Any]:
        """Submit the real proxied CAS form and detect WebVPN device verification."""
        self.active_mode = "webvpn"
        self._webvpn_sms_flow = None
        try:
            page, session_is_valid = self._open_webvpn_password_page()
            logger.info(
                "WebVPN password login page: status=%s final=%s",
                page.status_code,
                self._safe_url_metadata(page.url),
            )
            if not self._is_webvpn_login_url(page.url):
                if session_is_valid:
                    self._logged_in = True
                    self._save_cookies()
                    return {"status": "authenticated", "username": self.username or None}
                raise WebVPNLoginError("未能打开 WebVPN 统一认证页面")

            hidden = self._extract_hidden_fields(page.text)
            key_b64 = self._extract_rsa_key_from_html(page.text) or _RSA_PUBLIC_KEY_B64
            post_url = self._extract_login_form_action(page.text, page.url)
            form_data = self._build_login_form(hidden, key_b64)
            response = self._submit_login_form(hidden, key_b64, post_url, form_data)
            response.raise_for_status()

            challenge = self._extract_phone_challenge(response.text)
            logger.info(
                "WebVPN password submit: status=%s final=%s sms_challenge=%s",
                response.status_code,
                self._safe_url_metadata(response.url),
                bool(challenge),
            )
            if challenge:
                murmur, details = challenge
                self._webvpn_sms_flow = {
                    "id": str(uuid.uuid4()),
                    "device_url": urljoin(response.url, "device"),
                    "murmur": murmur,
                    "details": details,
                    "post_url": post_url,
                    "form_data": form_data,
                    "expires_at": time.time() + 180,
                }
                return {"status": "sms_required", "flow_id": self._webvpn_sms_flow["id"], "expires_in": 180}

            if self._is_webvpn_login_url(response.url):
                error = self._extract_error_message(response.text)
                raise NEULoginError(f"登录失败: {error}", _classify_login_error(error))

            self._sync_cas_cookie_to_webvpn({})
            if not self._webvpn_health_check():
                raise WebVPNLoginError("账号认证完成，但未能建立教务系统会话")
            self._logged_in = True
            self._save_cookies()
            return {"status": "authenticated", "username": self.username or None}
        except requests.exceptions.Timeout as error:
            raise WebVPNLoginError("WebVPN 登录请求超时，请检查网络或改用微信扫码快速登录") from error
        except requests.RequestException as error:
            raise WebVPNLoginError(
                f"WebVPN 请求失败（{type(error).__name__}）"
            ) from error

    def _open_webvpn_password_page(self) -> tuple[requests.Response, bool]:
        """Open the WebVPN CAS page, retrying once with a clean cookie jar."""
        for attempt in range(2):
            page = self._session.get(
                WEBVPN_ENTRY_URL,
                timeout=self.timeout,
                verify=self.verify_ssl,
                allow_redirects=True,
            )
            page.raise_for_status()
            if self._is_webvpn_login_url(page.url):
                return page, False
            if self._webvpn_health_check():
                return page, True
            if attempt == 0:
                logger.info(
                    "WebVPN entry reached an unexpected page; clearing session cookies and retrying once: %s",
                    self._safe_url_metadata(page.url),
                )
                self._session.cookies.clear()
        return page, False

    def send_webvpn_sms_code(self, flow_id: str) -> Dict[str, Any]:
        """Ask the official proxied CAS endpoint to send the SMS code."""
        flow = self._get_webvpn_sms_flow(flow_id)
        try:
            response = self._session.post(
                flow["device_url"], data={"m": "2"}, timeout=self.timeout,
                verify=self.verify_ssl, headers={"X-Requested-With": "XMLHttpRequest"},
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as error:
            raise WebVPNLoginError(
                f"发送短信验证码失败（{type(error).__name__}）"
            ) from error

        info = result.get("info")
        logger.info(
            "WebVPN SMS send response: status=%s info=%s keys=%s",
            response.status_code,
            info,
            sorted(result.keys()),
        )
        if info == "send":
            return {"status": "sent"}
        if info == "max":
            raise WebVPNLoginError("发送过于频繁，请稍后再试")
        if info == "unknow":
            raise WebVPNLoginError("统一认证未绑定手机号码，无法进行短信验证")
        raise WebVPNLoginError("短信验证码发送失败")

    def verify_webvpn_sms_code(self, flow_id: str, code: str, trust_device: bool = False) -> Dict[str, Any]:
        """Verify a code with device m=3, then submit the pending CAS form."""
        flow = self._get_webvpn_sms_flow(flow_id)
        try:
            response = self._session.post(
                flow["device_url"],
                data={
                    "d": flow["murmur"], "i": flow["details"], "m": "3",
                    "u": self.username, "c": code, "s": "1" if trust_device else "0",
                },
                timeout=self.timeout,
                verify=self.verify_ssl,
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            response.raise_for_status()
            info = response.json().get("info")
        except (requests.RequestException, ValueError) as error:
            raise WebVPNLoginError(
                f"验证短信验证码失败（{type(error).__name__}）"
            ) from error

        verification_result = info if info in {"ok", "most", "codeErr", "timeout"} else "other"
        logger.info(
            "WebVPN SMS verify response: status=%s result=%s",
            response.status_code,
            verification_result,
        )
        if info == "codeErr":
            raise WebVPNLoginError("验证码有误")
        if info == "timeout":
            raise WebVPNLoginError("验证码已超时，请重新开始登录")
        if info not in {"ok", "most"}:
            raise WebVPNLoginError("短信验证码验证失败")

        try:
            completion = self._submit_login_form({}, _RSA_PUBLIC_KEY_B64, flow["post_url"], flow["form_data"])
            completion.raise_for_status()
            logger.info(
                "WebVPN SMS completion submit: status=%s final=%s",
                completion.status_code,
                self._safe_url_metadata(completion.url),
            )
            self._sync_cas_cookie_to_webvpn({})
            if not self._webvpn_health_check():
                raise WebVPNLoginError("短信验证完成，但未能建立教务系统会话")
        finally:
            self._webvpn_sms_flow = None

        self._logged_in = True
        self._save_cookies()
        message = "设备数量已达上限，系统已解除最早的授信设备并完成登录" if info == "most" else "登录成功"
        return {"status": "authenticated", "username": self.username or None, "message": message}

    def cancel_webvpn_sms_login(self, flow_id: Optional[str] = None) -> None:
        if self._webvpn_sms_flow and (flow_id is None or self._webvpn_sms_flow["id"] == flow_id):
            self._webvpn_sms_flow = None

    def get_webvpn_qr_diagnostics(self) -> Dict[str, Any]:
        """Expose only non-secret QR redirect diagnostics for local debugging."""
        flow = self._webvpn_qr_flow or {}
        return {
            key: flow[key]
            for key in (
                "callback", "cookies_after_poll", "poll_set_cookies",
                "completion", "cookie_bridge", "health",
            )
            if key in flow
        }

    def _sync_cas_cookie_to_webvpn(self, diagnostics: Dict[str, Any]) -> None:
        """Mirror CASTGC into WebVPN's virtual pass.neu.edu.cn cookie store."""
        cas_cookie = next(
            (
                cookie for cookie in self._session.cookies
                if cookie.name == "CASTGC" and cookie.domain.lstrip(".") == "pass.neu.edu.cn"
            ),
            None,
        )
        if cas_cookie is None:
            diagnostics["cookie_bridge"] = {"attempted": False, "reason": "CASTGC missing"}
            return

        response = self._session.post(
            f"{WEBVPN_ORIGIN}/wengine-vpn/cookie",
            params={
                "method": "set",
                "host": "pass.neu.edu.cn",
                "scheme": "https",
                "path": "/tpass/login",
                # requests performs the same one-time URL encoding as the
                # gateway script's encodeURIComponent call.
                "ck_data": f"{cas_cookie.name}={cas_cookie.value}",
            },
            timeout=self.timeout,
            verify=self.verify_ssl,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        response.raise_for_status()
        diagnostics["cookie_bridge"] = {
            "attempted": True,
            "status_code": response.status_code,
            "response_is_json": response.headers.get("Content-Type", "").startswith("application/json"),
            "set_cookies": self._safe_set_cookie_names(response),
        }

    def _webvpn_health_check(self, diagnostics: Optional[Dict[str, Any]] = None) -> bool:
        health_url = "https://jwxt.neu.edu.cn/jwapp/sys/homeapp/api/home/currentUser.do"
        try:
            response = self._session_request(
                "POST",
                health_url,
                data={},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=min(self.timeout, 10),
                allow_redirects=True,
            )
            if diagnostics is not None:
                diagnostics["health"] = {
                    "status_code": response.status_code,
                    "final": self._safe_url_metadata(response.url),
                    "history": [
                        {
                            "status_code": item.status_code,
                            "location": self._safe_url_metadata(item.headers.get("Location", "")),
                        }
                        for item in response.history
                    ],
                    "cookies": self._safe_cookie_metadata(),
                }
            if self._is_webvpn_login_url(response.url):
                return False
            data = response.json()
            if diagnostics is not None:
                diagnostics["health"]["response_code"] = data.get("code")
            if data.get("code") != "0":
                return False
            user_data = data.get("datas", {})
            self.username = user_data.get("userId") or self.username
            return True
        except (requests.RequestException, ValueError):
            return False

    def ensure_login(self) -> bool:
        """
        确保已登录
        
        登录恢复优先级：
        1. 检查当前 session 是否有效（含协议回退）
        2. 尝试用 CAS Cookie 刷新票据（免密）
        3. 用账号密码重新登录（自动触发密钥刷新逻辑）
        
        Returns:
            是否成功登录
        """
        if self.active_mode == "webvpn":
            if self._webvpn_health_check():
                self._logged_in = True
                self._save_cookies()
                return True
            self._logged_in = False
            if not self.username or not self.password:
                return False

            logger.info("WebVPN Cookie 失效，静默尝试使用已保存的账号密码恢复...")
            try:
                result = self.start_webvpn_password_login()
            except NEULoginError as error:
                logger.warning(
                    "WebVPN 账号密码静默恢复失败，错误类型: %s",
                    getattr(error, "error_type", LOGIN_ERR_UNKNOWN),
                )
                return False

            if result.get("status") == "authenticated":
                self._logged_in = True
                return True

            # 短信验证属于交互式认证，静默恢复不能擅自发送短信或保留一个
            # 前端并不知道的流程。交给登录页重新发起完整认证。
            if result.get("status") == "sms_required":
                logger.info("WebVPN 静默恢复需要短信验证，转交登录页处理")
                self._webvpn_sms_flow = None
            self._logged_in = False
            return False

        if self._logged_in:
            # 测试当前会话是否有效
            try:
                time.sleep(0.1)
                resp = self._session_request(
                    "GET",
                    f"{self.target}/jwapp/sys/homeapp/api/home/currentUser.do",
                    timeout=5,
                    allow_redirects=False
                )
                # 302 跳到 CAS 说明 session 已失效
                if resp.status_code == 302 and "pass.neu.edu.cn" in resp.headers.get("Location", ""):
                    pass  # 会话失效，继续后续流程
                elif resp.status_code == 200:
                    try:
                        data = resp.json()
                        if data.get("code") == "0":
                            return True
                    except:
                        pass
                elif resp.status_code not in (301, 302, 303, 307, 308):
                    # 非重定向且非成功，可能是网络问题，仍尝试下一步
                    pass
            except:
                pass
            
            # Session 失效，标记为未登录
            logger.info("业务系统 Session 失效，尝试恢复...")
            self._logged_in = False
        
        # 第2步：尝试用 CAS Cookie 刷新票据（免密）
        if self._try_refresh_ticket(self.target):
            return True

        if not self.username or not self.password:
            return False
        
        # 第3步：用账号密码重新登录（会自动处理密钥刷新）
        logger.info("Cookie 失效，使用账号密码登录...")
        return self.login(self.target)

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        发送 HTTP 请求，自动处理票据失效
        
        Args:
            method: 请求方法
            url: 请求URL
            **kwargs: 其他参数
            
        Returns:
            Response 对象
        """
        # 确保已登录
        if not self._logged_in:
            if not self.ensure_login():
                if self.active_mode == "webvpn":
                    raise WebVPNRequiredError("WebVPN 会话无效，请重新扫码登录")
                raise NEULoginError("未登录或登录已过期")
        
        # 添加默认超时
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout
        
        # 发送请求（含协议回退）
        resp = self._session_request(method, url, **kwargs)
        
        # 检查是否需要重新登录
        # 有两种情况：
        # 1. allow_redirects=False 时直接看 302 状态码 + Location 头
        # 2. allow_redirects=True（默认）时，跟随重定向后最终落在 CAS 页面
        _redirected_to_cas = False
        allow_redirects = kwargs.get("allow_redirects", True)
        if not allow_redirects:
            # 直接看 302
            if resp.status_code in (301, 302, 303, 307, 308) and self._is_auth_redirect(resp.headers.get("Location", "")):
                _redirected_to_cas = True
        else:
            # 检查重定向历史中是否经过 CAS
            for r in resp.history:
                if r.status_code in (301, 302, 303, 307, 308) and self._is_auth_redirect(r.headers.get("Location", "")):
                    _redirected_to_cas = True
                    break
            # 也检查最终 URL 是否落在 CAS
            if not _redirected_to_cas and self._is_auth_redirect(resp.url):
                _redirected_to_cas = True
        
        if _redirected_to_cas:
            logger.info("检测到票据失效（重定向到认证页），重新登录...")
            self._logged_in = False
            if not self.ensure_login():
                if self.active_mode == "webvpn":
                    raise WebVPNRequiredError("WebVPN 会话已过期，请重新扫码登录")
                raise NEULoginError("统一认证会话已过期")
            # 重试原请求（含协议回退）
            resp = self._session_request(method, url, **kwargs)
        
        return resp

    def _is_auth_redirect(self, url: str) -> bool:
        parsed = urlparse(url)
        return (
            "pass.neu.edu.cn" in url
            or self._is_webvpn_login_url(url)
            or (parsed.hostname == "webvpn.neu.edu.cn" and parsed.path.startswith("/login"))
        )

    def get(self, url: str, **kwargs) -> requests.Response:
        """发送 GET 请求"""
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        """发送 POST 请求"""
        return self.request("POST", url, **kwargs)

    # ── 属性 ──────────────────────────────────────────────────────────────────

    @property
    def session(self) -> requests.Session:
        """获取底层 Session"""
        return self._session

    @property
    def cookies(self) -> dict:
        """获取当前 cookies"""
        result = {}
        for cookie in self._session.cookies:
            result[cookie.name] = cookie.value
        return result

    @property
    def is_logged_in(self) -> bool:
        """是否已登录"""
        return self._logged_in

    @property
    def academic(self):
        """成绩 API 入口"""
        if self._academic is None:
            from backend.core.academic.api import AcademicAPI
            self._academic = AcademicAPI(self)
        return self._academic

    @property
    def academic_report(self):
        """学业监测报告 API 入口"""
        if self._academic_report is None:
            from backend.core.academic.report import AcademicReportAPI
            self._academic_report = AcademicReportAPI(self)
        return self._academic_report

    @property
    def evaluation(self):
        """教学质量评价系统 API 入口（zljk.neu.edu.cn）"""
        if self._evaluation is None:
            from backend.core.evaluation.api import EvaluationAPI
            self._evaluation = EvaluationAPI(self)
        return self._evaluation

    def get_user_info(self) -> Dict[str, Any]:
        """
        获取当前用户信息
        
        Returns:
            {
                "user_name": str,      # 用户名
                "user_id": str,        # 学号
                "avatar_token": str,   # 头像Token
                "avatar_url": str,     # 头像URL
            }
        """
        url = "https://jwxt.neu.edu.cn/jwapp/sys/homeapp/api/home/currentUser.do"
        try:
            resp = self.post(url, data={}, headers={
                "Content-Type": "application/x-www-form-urlencoded"
            })
            data = resp.json()
            if data.get("code") == "0":
                user_data = data.get("datas", {})
                avatar_token = user_data.get("avatarToken", "")
                return {
                    "user_name": user_data.get("userName", ""),
                    "user_id": user_data.get("userId", ""),
                    "avatar_token": avatar_token,
                    "avatar_url": f"https://jwxt.neu.edu.cn/jwapp/sys/emapcomponent/file/getUploadedAttachment/{avatar_token}.do" if avatar_token else "",
                    "default_avatar": user_data.get("userImg", ""),
                }
        except Exception as e:
            logger.warning("获取用户信息失败: %s", type(e).__name__)
        return {}

    def get_avatar(self, avatar_token: str = None) -> Optional[bytes]:
        """
        获取用户头像图片
        
        流程：
        1. 获取头像文件信息
        2. 下载实际图片文件
        
        Args:
            avatar_token: 头像Token，不传则自动获取
            
        Returns:
            头像图片二进制数据，失败返回None
        """
        if not avatar_token:
            user_info = self.get_user_info()
            avatar_token = user_info.get("avatar_token")
        
        if not avatar_token:
            return None
        
        try:
            # 步骤1：获取文件信息
            file_info_url = f"https://jwxt.neu.edu.cn/jwapp/sys/emapcomponent/file/getUploadedAttachment/{avatar_token}.do"
            resp = self.get(file_info_url)
            logger.debug("头像文件信息状态: %s", resp.status_code)
            
            # 如果直接返回图片
            if resp.status_code == 200 and 'image' in resp.headers.get('Content-Type', ''):
                return resp.content
            
            # 尝试解析JSON获取实际文件URL
            try:
                data = resp.json()
                # 从 items 数组获取第一个文件的 fileUrl
                items = data.get('items', [])
                if items and len(items) > 0:
                    file_url = items[0].get('fileUrl')
                    if file_url:
                        # fileUrl 是相对路径，需要拼接域名
                        if file_url.startswith('/'):
                            download_url = f"https://jwxt.neu.edu.cn{file_url}"
                        else:
                            download_url = file_url
                        
                        resp = self.get(download_url)
                        logger.debug(
                            "头像下载状态: %s, Content-Type: %s",
                            resp.status_code,
                            resp.headers.get("Content-Type", ""),
                        )
                        
                        if resp.status_code == 200:
                            return resp.content
                else:
                    logger.debug("头像文件信息不含 items")
                    
            except ValueError:
                # 不是JSON，可能是直接图片数据
                logger.debug("头像文件信息不是 JSON")
                if resp.status_code == 200:
                    return resp.content
                    
        except Exception as error:
            logger.error("获取头像失败: %s", type(error).__name__)
        return None

    # ── Cookie 持久化 ─────────────────────────────────────────────────────────
    
    def _save_cookies(self) -> bool:
        """
        保存 CAS Cookie 到文件
        
        Returns:
            是否成功保存
        """
        if not self.cookie_file:
            return False
        
        try:
            cookies = []
            for cookie in self._session.cookies:
                domain = cookie.domain.lstrip(".")
                if domain.endswith("neu.edu.cn"):
                    cookies.append({
                        "name": cookie.name,
                        "value": cookie.value,
                        "domain": cookie.domain,
                        "path": cookie.path,
                        "expires": cookie.expires,
                        "secure": cookie.secure,
                    })

            if cookies:
                temporary_file = f"{self.cookie_file}.tmp"
                with open(temporary_file, "w", encoding="utf-8") as f:
                    json.dump({
                        "version": 2,
                        "username": self.username,
                        "active_mode": self.active_mode,
                        "cookies": cookies,
                        "saved_at": time.time(),
                    }, f, ensure_ascii=False)
                os.replace(temporary_file, self.cookie_file)
                if os.name != "nt":
                    os.chmod(self.cookie_file, 0o600)
                logger.debug(f"Cookie 已保存到 {self.cookie_file}")
            return True
        except Exception as e:
            logger.warning(f"保存 Cookie 失败: {e}")
            return False
    
    def _load_cookies(self) -> bool:
        """
        从文件加载 CAS Cookie
        
        Returns:
            是否成功加载
        """
        if not self.cookie_file:
            return False
        
        try:
            import os
            if not os.path.exists(self.cookie_file):
                return False
            
            with open(self.cookie_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # 检查用户名是否匹配
            saved_username = data.get("username", "")
            if self.username and saved_username and saved_username != self.username:
                logger.debug("Cookie 用户名不匹配")
                return False
            if not self.username:
                self.username = saved_username
            self.active_mode = data.get("active_mode", self.active_mode)
            
            # 恢复 cookies
            from requests.cookies import create_cookie
            for cookie_data in data.get("cookies", []):
                cookie = create_cookie(
                    name=cookie_data["name"],
                    value=cookie_data["value"],
                    domain=cookie_data["domain"],
                    path=cookie_data["path"],
                    expires=cookie_data.get("expires"),
                    secure=cookie_data.get("secure", False),
                )
                self._session.cookies.set_cookie(cookie)
            
            logger.debug(f"Cookie 已从 {self.cookie_file} 加载")
            return True
        except Exception as e:
            logger.warning(f"加载 Cookie 失败: {e}")
            return False
    
    def clear_cookies(self) -> None:
        """清除保存的 Cookie"""
        if self.cookie_file:
            if os.path.exists(self.cookie_file):
                os.remove(self.cookie_file)
                logger.debug(f"Cookie 文件已删除: {self.cookie_file}")
        self._session.cookies.clear()
        self._logged_in = False

    # ── CAS 票据刷新 ──────────────────────────────────────────────────────────
    
    def _try_refresh_ticket(self, target: str = None) -> bool:
        """
        尝试用现有的 CAS Cookie 获取新票据
        
        当业务系统 session 失效但 CAS Cookie 还有效时，
        可以用此方法免密获取新票据。
        
        Args:
            target: 目标系统 URL
            
        Returns:
            是否成功获取新票据
        """
        if target is None:
            target = self.target
        
        service_url = self._resolve_service_url(target)
        login_url = f"{CAS_LOGIN_URL}?service={requests.utils.quote(service_url, safe='')}"
        
        logger.info("尝试用 Cookie 刷新票据...")
        
        try:
            # 访问 CAS 登录页，如果 Cookie 有效，会直接重定向回业务系统
            resp = self._session.get(
                login_url,
                timeout=self.timeout,
                verify=self.verify_ssl,
                allow_redirects=True,
            )
            resp.raise_for_status()
            
            final_url = resp.url
            final_domain = urlparse(final_url).netloc
            cas_domain = urlparse(CAS_LOGIN_URL).netloc
            
            # 如果最终 URL 不是 CAS 登录页，说明成功获取了票据
            if final_domain != cas_domain:
                logger.info("票据刷新成功，目标域名: %s", final_domain)
                self._logged_in = True
                self._save_cookies()  # 保存更新后的 cookies
                return True
            else:
                # 还在 CAS 页面，说明 Cookie 也失效了
                logger.debug("CAS Cookie 已失效，需要重新登录")
                return False
                
        except Exception as e:
            logger.warning("票据刷新失败: %s", type(e).__name__)
            return False

    # ── 内部方法 ───────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_service_url(target: str) -> str:
        """
        解析 CAS service URL
        
        自动使用 target 的协议（http/https），
        以适配目标服务器的协议切换。
        """
        parsed = urlparse(target)
        host = parsed.netloc.lower()
        scheme = parsed.scheme or "https"
        
        if "jwxt.neu.edu.cn" in host:
            return f"{scheme}://jwxt.neu.edu.cn/jwapp/sys/homeapp/index.do"
        
        return target

    @staticmethod
    def _extract_hidden_fields(html: str) -> dict:
        """提取隐藏表单字段"""
        soup = BeautifulSoup(html, "lxml")
        fields = {}
        for inp in soup.find_all("input", type="hidden"):
            name = inp.get("name")
            value = inp.get("value", "")
            if name:
                fields[name] = value
        return fields

    @staticmethod
    def _extract_error_message(html: str) -> str:
        """提取错误信息"""
        soup = BeautifulSoup(html, "lxml")
        for selector in ["#errormsg", ".error", "#errormsghide", ".alert"]:
            el = soup.select_one(selector)
            if el and el.get_text(strip=True):
                return el.get_text(strip=True)
        return "未知错误"

    @staticmethod
    def _swap_protocol(url: str) -> str:
        """交换 URL 的 HTTP/HTTPS 协议"""
        if url.startswith("https://"):
            return "http://" + url[8:]
        elif url.startswith("http://"):
            return "https://" + url[7:]
        return url

    @staticmethod
    def _extract_rsa_key_from_html(html: str) -> Optional[str]:
        """
        从 CAS 登录页 HTML 中提取 RSA 公钥
        
        公钥可能出现在以下位置：
        1. 内联 JS 变量: var/const/let publicKeyStr = "MIIBIjANBg..."
        2. 隐藏表单域: <input type="hidden" id="publicKey" value="MIIBIjANBg...">
        
        Returns:
            公钥 Base64 字符串，未找到返回 None
        """
        patterns = [
            # JS 变量赋值（覆盖 var/const/let，单引号/双引号）
            r'(?:var|const|let)\s+publicKeyStr\s*=\s*["\']([A-Za-z0-9+/=]+)["\']',
            r'(?:var|const|let)\s+publicKey\s*=\s*["\']([A-Za-z0-9+/=]+)["\']',
            # 隐藏表单域
            r'<input[^>]*id=["\']publicKey["\'][^>]*value=["\']([A-Za-z0-9+/=]+)["\']',
            r'<input[^>]*value=["\']([A-Za-z0-9+/=]+)["\'][^>]*id=["\']publicKey["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                key = match.group(1)
                if len(key) > 100:  # RSA 公钥长度阈值
                    logger.debug(f"从登录页 HTML 提取到 RSA 公钥，长度: {len(key)}")
                    return key
        return None

    def _session_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        发送 HTTP 请求，对 jwxt.neu.edu.cn 自动进行协议回退
        
        当目标服务器在 HTTP/HTTPS 之间切换时：
        1. 优先使用已知的可用协议（_protocol_override）
        2. 连接失败时自动切换协议重试
        3. 回退成功后记住可用协议，后续请求直接使用
        """
        # WebVPN 模式下，业务层仍传原始校内 URL；在此处统一转换。
        if self.active_mode == "webvpn" and not WebVPNUrlCodec.is_webvpn_url(url):
            hostname = urlparse(url).hostname or ""
            if hostname.endswith(".neu.edu.cn"):
                url = WebVPNUrlCodec.convert_url(url)
                headers = dict(kwargs.get("headers") or {})
                referer = headers.get("Referer")
                if referer and not WebVPNUrlCodec.is_webvpn_url(referer):
                    referer_host = urlparse(referer).hostname or ""
                    if referer_host.endswith(".neu.edu.cn"):
                        headers["Referer"] = WebVPNUrlCodec.convert_url(referer)
                origin = headers.get("Origin")
                if origin and (urlparse(origin).hostname or "").endswith(".neu.edu.cn"):
                    headers["Origin"] = WEBVPN_ORIGIN
                kwargs["headers"] = headers

        # 应用已知可用协议
        if self._protocol_override and "jwxt.neu.edu.cn" in url:
            current_scheme = "https://" if url.startswith("https://") else "http://"
            if current_scheme != self._protocol_override:
                url = self._protocol_override + url[len(current_scheme):]
        
        try:
            return self._session.request(method, url, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.SSLError,
                requests.exceptions.Timeout, requests.exceptions.TooManyRedirects) as e:
            # 仅对 jwxt.neu.edu.cn 进行协议回退
            if "jwxt.neu.edu.cn" not in url:
                raise
            
            alt_url = self._swap_protocol(url)
            logger.info(
                "请求域名 %s 失败 (%s)，尝试协议回退 %s -> %s",
                urlparse(url).hostname or "unknown",
                type(e).__name__,
                urlparse(url).scheme,
                urlparse(alt_url).scheme,
            )
            resp = self._session.request(method, alt_url, **kwargs)
            # 记住可用协议，后续请求直接使用
            self._protocol_override = "https://" if alt_url.startswith("https://") else "http://"
            logger.info(f"协议回退成功，后续请求将使用 {self._protocol_override}")
            return resp


# ── 异常 ──────────────────────────────────────────────────────────────────────
# NEULoginError 已在上方定义（class 需在 raise 之前先定义）
