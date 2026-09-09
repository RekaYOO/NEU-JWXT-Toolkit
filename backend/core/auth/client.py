"""
neu_auth/client.py
==================
东北大学统一身份认证（CAS）登录客户端

特性：
- HTTP/HTTPS 协议自动回退（目标服务协议切换时自动适配）
- 动态密钥刷新（登录时从页面提取最新公钥，失败时自动从服务器获取）
- 网络故障与认证失败分类
- 票据失效自动重新登录
- CAS Cookie 持久化（免密刷新票据）
- 请求限流保护
"""

import base64
import json
import os
import random
import re
import threading
import time
import logging
import uuid
from http.cookies import SimpleCookie
from typing import Optional, Dict, Any
from urllib.parse import urlencode, urljoin, urlparse, parse_qs, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

from backend.core.network import WEBVPN_ENTRY_URL, WEBVPN_ORIGIN, WebVPNUrlCodec
from backend.core.auth.session_manager import is_remote_read_context, remote_read_bypass

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────

CAS_BASE_URL = "https://pass.neu.edu.cn/tpass"
CAS_LOGIN_URL = f"{CAS_BASE_URL}/login"

# WebVPN's injected browser code adds these routing queries to relative
# requests made by the proxied CAS page.  Without them the gateway may treat
# the request as belonging to the WebVPN origin instead of pass.neu.edu.cn.
_WEBVPN_CAS_HOST = "pass.neu.edu.cn"
_WEBVPN_CAS_PROTOCOL = "https"
_WEBVPN_IMAGE_QUERY = "vpn-1"


def _prepend_query(url: str, query: str) -> str:
    """Prepend a gateway query while preserving the URL's own query/hash."""
    parsed = urlsplit(url)
    existing = parsed.query
    merged = query if not existing else f"{query}&{existing}"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, merged, parsed.fragment))

# Extra campus services deliberately use an explicit allow-list.  Callers may
# choose a service and a relative path, but can never turn the shared, logged-in
# session into an arbitrary HTTP client.
SERVICE_CONFIGS = {
    "cxcy": {
        "origin": "https://cxcy.neu.edu.cn",
        "service": "https://cxcy.neu.edu.cn/ucenter/auth/caslogin?type=student",
        "host": "cxcy.neu.edu.cn",
        "network_modes": ("direct", "webvpn"),
        "probe_path": "/popscience/comp/ucenter/main/index",
        "auth_html_markers": ("统一身份认证", "password"),
        "independent_identity_recovery": True,
        "allowed_prefixes": (
            "/popscience/comp/ucenter/",
            "/originality/comp/ucenter/",
            "/technical/comp/ucenter/",
            "/business/comp/ucenter/",
            "/popscience/comp/front/comp/info",
            "/originality/comp/front/comp/info",
            "/technical/comp/front/comp/info",
            "/business/comp/front/comp/info",
            "/static/uploads/",
        ),
        "login_paths": (
            "/ucenter/index/login",
            "/ucenter/auth/cas",
            "/ucenter/auth/caslogin",
        ),
    },
    "jwxk": {
        "origin": "https://jwxk.neu.edu.cn",
        "service": "https://jwxk.neu.edu.cn/xsxk/auth/cas",
        "host": "jwxk.neu.edu.cn",
        "network_modes": ("direct", "webvpn"),
        "allowed_prefixes": ("/xsxk/",),
        "login_paths": ("/xsxk/auth/cas",),
        "token_cookie": "token",
        "token_header": "Authorization",
        "auth_response_codes": ("401", "402", "403"),
        # JWXK also uses 401-like business codes for feeds or course scopes
        # that do not apply to the active round.  Only messages that actually
        # describe identity/token expiry may trigger CAS recovery.
        "auth_response_message_markers": (
            "登录", "认证", "未授权", "授权失效", "会话", "token", "令牌",
        ),
        # These responses can contain wording such as “请登录后再试”, but the
        # actual condition is school-side throttling.  Rebuilding CAS here
        # creates a retry storm and makes the cooldown longer.
        "auth_response_message_exclusions": (
            "请求过快", "请求频繁", "访问频繁", "操作频繁",
        ),
        "json_prefixes": (
            "/xsxk/elective/",
            "/xsxk/volunteer/",
            "/xsxk/web/now",
            "/xsxk/web/studentInfo",
        ),
        "access_denied_markers": (
            {
                "markers": ("学生不在选课轮次中", "暂时不能登录"),
                "error_code": "JWXK_NOT_IN_SELECTION_ROUND",
                "message": "当前账号不在学校开放的选课轮次中，暂时不能进入选课系统。",
            },
        ),
    },
}

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


# 登录错误类型
LOGIN_ERR_WRONG_PWD = "WRONG_PASSWORD"   # 密码错误
LOGIN_ERR_BAD_KEY = "BAD_KEY"             # 公钥/加密错误
LOGIN_ERR_UNKNOWN = "UNKNOWN"             # 未知错误

# Stable, frontend-facing WebVPN error codes.  Messages remain localized and
# backwards compatible; callers should branch on these codes instead.
WEBVPN_ERR_FLOW_MISSING = "WEBVPN_FLOW_MISSING"
WEBVPN_ERR_FLOW_REPLACED = "WEBVPN_FLOW_REPLACED"
WEBVPN_ERR_FLOW_EXPIRED = "WEBVPN_FLOW_EXPIRED"
WEBVPN_ERR_CAPTCHA_FETCH = "WEBVPN_CAPTCHA_FETCH_FAILED"
WEBVPN_ERR_CAPTCHA_INVALID = "WEBVPN_CAPTCHA_INVALID"
WEBVPN_ERR_SMS_RATE_LIMITED = "WEBVPN_SMS_RATE_LIMITED"
WEBVPN_ERR_SMS_UNBOUND = "WEBVPN_SMS_PHONE_UNBOUND"
WEBVPN_ERR_SMS_INVALID = "WEBVPN_SMS_INVALID"
WEBVPN_ERR_UPSTREAM_TIMEOUT = "WEBVPN_UPSTREAM_TIMEOUT"
WEBVPN_ERR_UPSTREAM_NON_JSON = "WEBVPN_UPSTREAM_NON_JSON"
WEBVPN_ERR_UPSTREAM_REDIRECT = "WEBVPN_UPSTREAM_REDIRECT"
WEBVPN_ERR_SESSION_ESTABLISH = "WEBVPN_SESSION_ESTABLISH_FAILED"
WEBVPN_ERR_CAMPUS_NETWORK = "WEBVPN_CAMPUS_NETWORK_BLOCKED"
WEBVPN_ERR_UNKNOWN = "WEBVPN_UNKNOWN_ERROR"

# The school describes SMS codes as having an approximately five-minute
# validity window.  Keep that as the user-facing countdown, but retain a short
# server-side grace period so clock skew, page scheduling and a request already
# in flight do not make the toolkit reject a code before the official service
# has had a chance to decide.
WEBVPN_SMS_VALIDITY_SECONDS = 300
WEBVPN_SMS_FLOW_GRACE_SECONDS = 60

_WEBVPN_CAMPUS_BLOCK_MARKERS = (
    "WebVPN仅用于我校师生在校外登录",
    "校园网用户无需使用WebVPN",
)
_WEBVPN_CAMPUS_BLOCK_MESSAGE = (
    "检测到当前处于校园网环境，学校 WebVPN 拒绝校园网访问。"
    "请切换为“校内直连”后登录。"
)


# ── 工具函数 ──────────────────────────────────────────────────────────────────

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


def _public_login_error_message(error_msg: str, error_type: str) -> str:
    """Return a useful login error without exposing misleading account detail."""
    if error_type == LOGIN_ERR_WRONG_PWD:
        return "账号或密码错误"
    return f"登录失败: {error_msg}" if error_msg else "登录失败"


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
    def __init__(
        self,
        message: str,
        error_type: str = LOGIN_ERR_UNKNOWN,
        error_code: Optional[str] = None,
    ):
        super().__init__(message)
        self.error_type = error_type
        self.error_code = error_code


class WebVPNRequiredError(NEULoginError):
    """Direct campus access is unavailable and WebVPN authentication is needed."""


class WebVPNLoginError(NEULoginError):
    """The WebVPN QR login flow could not be completed."""


class DirectAccessError(NEULoginError):
    """A direct-campus request could not reach the academic system."""


class ServiceAccessError(NEULoginError):
    """The campus identity is valid, but the business system denied access."""

    def __init__(self, message: str, *, service: str, error_code: str):
        super().__init__(message)
        self.service = service
        self.error_code = error_code


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
        # Protect the primary cookie jar while isolated shared-read sessions
        # snapshot/merge state and while auth/mutation code updates cookies.
        self._session_state_lock = threading.RLock()
        # Read requests may run concurrently, but an expired session must only
        # start one CAS/WebVPN recovery chain at a time.
        self._auth_operation_lock = threading.RLock()
        self._auth_recovery_generation = 0
        self._logged_in = False
        self._academic = None
        self._academic_report = None  # 学业监测报告 API
        self._evaluation = None       # 教学质量评价系统 API
        self._timetable = None        # 课表查询 API
        self._webvpn_qr_flow: Optional[Dict[str, Any]] = None
        self._webvpn_sms_flow: Optional[Dict[str, Any]] = None
        self._last_webvpn_error_code = ""
        self._last_webvpn_error_message = ""
        self._last_webvpn_error_at = 0.0
        self._service_token_cache: Dict[tuple[str, str], str] = {}
        
        # 自动恢复入口可以读取历史会话；用户主动登录必须从干净会话开始，
        # 避免旧 WebVPN Cookie 将新登录导向错误页面。
        if cookie_file and restore_session:
            self._load_cookies()

    def login(self, target: str = "https://jwxt.neu.edu.cn") -> bool:
        """
        执行直连 CAS 登录
        
        登录策略：
        1. 使用目标服务 URL，优先从登录页面提取最新 RSA 公钥
        2. 明确的认证拒绝可按原有规则核验公钥，网络异常不重放登录
        3. 直连不可达及时返回 WebVPN 建议；后台恢复退避由会话管理器统一处理
        
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
        raise NEULoginError(
            _public_login_error_message(error_msg, error_type),
            error_type=error_type,
        )

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
                error_message = self._find_login_error_message(resp2.text)
                if not error_message:
                    raise DirectAccessError(
                        "直连认证未能进入教务系统，请检查校园网络；校外请切换 WebVPN 模式"
                    )
                return error_message
            
            return None  # 登录成功
            
        except requests.RequestException as e:
            # A lost response is not a key rejection and must not replay the form.
            raise DirectAccessError("直连登录请求失败，请检查校园网络；校外请切换 WebVPN 模式") from e

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
    def _is_webvpn_campus_block_response(response: requests.Response) -> bool:
        """Recognize the gateway's campus-network rejection page."""
        try:
            status_code = int(getattr(response, "status_code", 0) or 0)
        except (TypeError, ValueError):
            return False
        if status_code != 403:
            return False
        hostname = (
            urlparse(str(getattr(response, "url", "") or "")).hostname or ""
        ).lower()
        if hostname != "webvpn.neu.edu.cn":
            return False
        try:
            body = str(response.text or "")[:8192]
        except (AttributeError, TypeError, ValueError):
            return False
        return any(marker in body for marker in _WEBVPN_CAMPUS_BLOCK_MARKERS)

    def _raise_for_webvpn_response(self, response: requests.Response) -> None:
        """Raise stable WebVPN errors without exposing upstream HTML."""
        self._raise_if_webvpn_campus_block(response)
        response.raise_for_status()

    def _raise_if_webvpn_campus_block(self, response: requests.Response) -> None:
        """Classify only the special campus block; preserve other statuses."""
        if self._is_webvpn_campus_block_response(response):
            self._last_webvpn_error_code = WEBVPN_ERR_CAMPUS_NETWORK
            self._last_webvpn_error_message = _WEBVPN_CAMPUS_BLOCK_MESSAGE
            self._last_webvpn_error_at = time.time()
            raise WebVPNLoginError(
                _WEBVPN_CAMPUS_BLOCK_MESSAGE,
                error_code=WEBVPN_ERR_CAMPUS_NETWORK,
            )
        hostname = (
            urlparse(str(getattr(response, "url", "") or "")).hostname or ""
        ).lower()
        if hostname == "webvpn.neu.edu.cn":
            self._last_webvpn_error_code = ""
            self._last_webvpn_error_message = ""
            self._last_webvpn_error_at = 0.0

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

    def start_webvpn_qr_login(
        self, expires_in: int = 180, *, target_service: str = "primary",
    ) -> Dict[str, Any]:
        """Create a QR login flow bound to this client's requests session."""
        expires_in = max(60, min(int(expires_in), 600))
        self.active_mode = "webvpn"
        service = WEBVPN_ENTRY_URL
        direct_login_url = f"{CAS_LOGIN_URL}?service={requests.utils.quote(service, safe='')}"
        try:
            response = self._session.get(
                direct_login_url,
                timeout=self.timeout,
                verify=self.verify_ssl,
                allow_redirects=True,
            )
            self._raise_for_webvpn_response(response)
        except requests.Timeout as error:
            raise WebVPNLoginError(
                "获取 WebVPN 二维码超时，请稍后重试",
                error_code=WEBVPN_ERR_UPSTREAM_TIMEOUT,
            ) from error
        except requests.RequestException as error:
            raise WebVPNLoginError(
                "获取 WebVPN 二维码失败，请稍后重试",
                error_code=WEBVPN_ERR_UNKNOWN,
            ) from error

        parsed_login_url = urlparse(response.url)
        if parsed_login_url.hostname != "pass.neu.edu.cn" or "/tpass/login" not in parsed_login_url.path:
            raise WebVPNLoginError("WebVPN 统一认证页面未处于二维码登录状态", error_code=WEBVPN_ERR_UPSTREAM_REDIRECT)

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
            "target_service": target_service if target_service in SERVICE_CONFIGS else "primary",
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
            raise WebVPNLoginError("二维码登录流程不存在或已被替换", error_code=WEBVPN_ERR_FLOW_REPLACED)
        if time.time() >= flow["expires_at"]:
            self._webvpn_qr_flow = None
            return {"status": "expired"}

        status_url = (
            f"{flow['qr_status_url']}?"
            f"{urlencode({'random': time.time(), 'uuid': flow['uuid']})}"
        )
        try:
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
            self._raise_for_webvpn_response(response)
        except requests.Timeout as error:
            raise WebVPNLoginError(
                "二维码状态检查超时，请稍后重试",
                error_code=WEBVPN_ERR_UPSTREAM_TIMEOUT,
            ) from error
        except requests.RequestException as error:
            raise WebVPNLoginError(
                "二维码状态检查失败，请稍后重试",
                error_code=WEBVPN_ERR_UNKNOWN,
            ) from error
        if not response.text.strip():
            # The production endpoint deliberately returns an empty body while
            # the QR code has not been scanned yet.
            return {"status": "pending"}
        try:
            result = response.json()
        except ValueError as exc:
            raise WebVPNLoginError("二维码状态接口返回了非 JSON 响应", error_code=WEBVPN_ERR_UPSTREAM_NON_JSON) from exc
        if not isinstance(result, dict):
            raise WebVPNLoginError(
                "二维码状态接口返回了无法识别的响应",
                error_code=WEBVPN_ERR_UPSTREAM_NON_JSON,
            )

        redirect_url = result.get("redirect_url")
        if not redirect_url:
            return {"status": "pending"}

        callback_url = urljoin(flow["login_page_url"], redirect_url)
        flow["callback"] = self._safe_url_metadata(callback_url)
        flow["cookies_after_poll"] = self._safe_cookie_metadata()
        flow["poll_set_cookies"] = self._safe_set_cookie_names(response)
        callback_host = urlparse(callback_url).hostname
        if callback_host not in {"pass.neu.edu.cn", "webvpn.neu.edu.cn"}:
            raise WebVPNLoginError("二维码登录返回了不受信任的跳转地址", error_code=WEBVPN_ERR_UPSTREAM_REDIRECT)

        # Match the official page: follow the CAS redirect first, then let its
        # service ticket establish the WebVPN session.  Converting a pass URL
        # before this request breaks the ticket callback chain.
        try:
            completion = self._session.get(
                callback_url,
                timeout=self.timeout,
                verify=self.verify_ssl,
                allow_redirects=True,
            )
            self._raise_for_webvpn_response(completion)
        except requests.Timeout as error:
            raise WebVPNLoginError(
                "二维码回调超时，请稍后重试",
                error_code=WEBVPN_ERR_UPSTREAM_TIMEOUT,
            ) from error
        except requests.RequestException as error:
            raise WebVPNLoginError(
                "二维码回调失败，请稍后重试",
                error_code=WEBVPN_ERR_UNKNOWN,
            ) from error
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
        challenge = self._extract_second_auth_form(completion.text, completion.url)
        if challenge:
            self._webvpn_qr_flow = None
            result = self._create_webvpn_second_auth_flow(
                challenge,
                source="qr",
                remember=False,
                target_service=str(flow.get("target_service") or "primary"),
            )
            return result
        self._sync_cas_cookie_to_webvpn(flow)

        # WebVPN may leave the browser on its proxied CAS page even after it
        # has issued the gateway ticket cookie.  The actual success criterion
        # is whether that cookie can establish the target JWXT session.
        target_service = str(flow.get("target_service") or "primary")
        self._verify_service_qr_identity(target_service)
        target = self._verify_webvpn_login_target(target_service, flow)
        if not target["authenticated"]:
            message = (
                "扫码已完成，但未能建立选课系统的 WebVPN 会话"
                if target_service == "jwxk"
                else "扫码已完成，但未能建立教务系统会话"
            )
            raise WebVPNLoginError(message, error_code=WEBVPN_ERR_SESSION_ESTABLISH)

        self._logged_in = True
        self._webvpn_qr_flow = None
        self._save_cookies()
        return {
            "status": "authenticated", "username": self.username or None,
            "target_service": target_service,
            "service_auth_state": target.get("service_auth_state", "authenticated"),
        }

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

    @staticmethod
    def _extract_second_auth_form(html: str, page_url: str) -> Optional[Dict[str, Any]]:
        """Extract the current WebVPN device second-auth form.

        The page currently uses ``second_auth_form`` rather than the older
        JavaScript ``phone(...)`` challenge.  Keep all values in memory only;
        callers must never persist the returned form data.
        """
        soup = BeautifulSoup(html, "lxml")
        form = soup.select_one("form#second_auth_form, form[name='second_auth_form']")
        if form is None:
            return None
        hidden = {}
        for field in form.select("input[name]"):
            name = field.get("name")
            if name:
                hidden[name] = field.get("value", "")
        action = urljoin(page_url, form.get("action") or page_url)
        return {
            "form_action": action,
            "page_url": page_url,
            "hidden_fields": hidden,
        }

    @staticmethod
    def _build_webvpn_captcha_url(page_url: str) -> str:
        """Build the real CAPTCHA endpoint used by the proxied CAS page.

        The image element in the second-auth HTML can point at a static
        preview.  The browser requests ``code?vpn-1&<random>`` from the same
        proxied ``/tpass/`` directory, so the backend must reproduce that
        request instead of trusting the element's ``src`` attribute.
        """
        parsed = urlsplit(page_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "webvpn.neu.edu.cn"
            or "/tpass/" not in parsed.path
        ):
            raise WebVPNLoginError("图形验证码页面地址无效，请重新登录", error_code=WEBVPN_ERR_UPSTREAM_REDIRECT)
        endpoint = urljoin(page_url, "code")
        return f"{endpoint}?{_WEBVPN_IMAGE_QUERY}&{random.random():.16f}"

    @staticmethod
    def _webvpn_captcha_media_type(response: requests.Response) -> str:
        media_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        supported = {"image/jpeg", "image/png", "image/webp", "image/gif"}
        if media_type not in supported or not response.content:
            raise WebVPNLoginError("学校未返回有效的图形验证码，请刷新后重试", error_code=WEBVPN_ERR_CAPTCHA_FETCH)
        # The current gateway has been observed to label GIF bytes as
        # ``image/jpeg``.  Prefer the actual magic bytes when recognizable so
        # strict browsers can decode the data URL reliably, while retaining
        # the server type for otherwise valid JPEG/PNG/WebP responses.
        content = bytes(response.content)
        detected = None
        if content.startswith((b"GIF87a", b"GIF89a")):
            detected = "image/gif"
        elif content.startswith(b"\x89PNG\r\n\x1a\n"):
            detected = "image/png"
        elif content.startswith(b"\xff\xd8\xff"):
            detected = "image/jpeg"
        elif content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            detected = "image/webp"
        return detected or media_type

    def _fetch_webvpn_captcha(self, flow: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch the real CAPTCHA image for a pending flow."""
        captcha_url = self._build_webvpn_captcha_url(flow["page_url"])
        try:
            response = self._session.get(
                captcha_url,
                timeout=self.timeout,
                verify=self.verify_ssl,
                headers={
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Referer": flow["page_url"],
                    "Cache-Control": "no-cache",
                },
            )
            self._raise_for_webvpn_response(response)
        except requests.RequestException as error:
            code = WEBVPN_ERR_UPSTREAM_TIMEOUT if isinstance(error, requests.Timeout) else WEBVPN_ERR_CAPTCHA_FETCH
            raise WebVPNLoginError(
                f"获取图形验证码失败（{type(error).__name__}）",
                error_code=code,
            ) from error
        final_url = urlsplit(str(getattr(response, "url", "") or captcha_url))
        if (
            final_url.scheme != "https"
            or final_url.hostname != "webvpn.neu.edu.cn"
            or not final_url.path.endswith("/tpass/code")
        ):
            raise WebVPNLoginError("图形验证码请求被重定向，请重新登录", error_code=WEBVPN_ERR_UPSTREAM_REDIRECT)
        media_type = self._webvpn_captcha_media_type(response)
        flow["captcha_image"] = base64.b64encode(response.content).decode("ascii")
        flow["captcha_media_type"] = media_type
        flow["captcha_code"] = ""
        flow["captcha_fetched_at"] = time.time()
        return {
            "captcha_image": f"data:{media_type};base64,{flow['captcha_image']}",
        }

    def _create_webvpn_second_auth_flow(
        self,
        challenge: Dict[str, Any],
        *,
        source: str,
        remember: bool = False,
        target_service: str = "primary",
    ) -> Dict[str, Any]:
        now = time.time()
        flow = {
            "id": str(uuid.uuid4()),
            "source": source,
            "network_mode": "webvpn",
            "form_action": challenge["form_action"],
            "page_url": challenge["page_url"],
            "hidden_fields": dict(challenge.get("hidden_fields") or {}),
            "code_expires_at": now + WEBVPN_SMS_VALIDITY_SECONDS,
            "expires_at": (
                now
                + WEBVPN_SMS_VALIDITY_SECONDS
                + WEBVPN_SMS_FLOW_GRACE_SECONDS
            ),
            "remember": bool(remember),
            "target_service": target_service if target_service in SERVICE_CONFIGS else "primary",
        }
        self._webvpn_sms_flow = flow
        result = self._fetch_webvpn_captcha(flow)
        return {
            "status": "sms_required",
            "flow_id": flow["id"],
            "expires_in": WEBVPN_SMS_VALIDITY_SECONDS,
            **result,
        }

    @staticmethod
    def _renew_webvpn_sms_window(flow: Dict[str, Any]) -> int:
        """Renew the nominal SMS window after a user refresh/send action."""
        now = time.time()
        flow["code_expires_at"] = now + WEBVPN_SMS_VALIDITY_SECONDS
        flow["expires_at"] = (
            flow["code_expires_at"] + WEBVPN_SMS_FLOW_GRACE_SECONDS
        )
        return WEBVPN_SMS_VALIDITY_SECONDS

    def _get_webvpn_sms_flow(self, flow_id: str) -> Dict[str, Any]:
        flow = self._webvpn_sms_flow
        if not flow or flow.get("id") != flow_id:
            raise WebVPNLoginError("短信验证流程不存在或已被替换", error_code=WEBVPN_ERR_FLOW_REPLACED)
        try:
            expires_at = float(flow.get("expires_at", 0))
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at <= 0 or time.time() >= expires_at:
            self._webvpn_sms_flow = None
            raise WebVPNLoginError("短信验证码登录已过期，请重新输入账号密码", error_code=WEBVPN_ERR_FLOW_EXPIRED)
        return flow

    def start_webvpn_password_login(self, *, target_service: str = "primary") -> Dict[str, Any]:
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
                    target = (
                        self._verify_webvpn_login_target(target_service)
                        if target_service in SERVICE_CONFIGS else {"authenticated": True}
                    )
                    if not target["authenticated"]:
                        raise WebVPNLoginError(
                            "WebVPN 会话核验失败", error_code=WEBVPN_ERR_SESSION_ESTABLISH,
                        )
                    self._logged_in = True
                    self._save_cookies()
                    return {
                        "status": "authenticated", "username": self.username or None,
                        "target_service": target_service,
                        "service_auth_state": target.get("service_auth_state", "authenticated"),
                    }
                raise WebVPNLoginError("未能打开 WebVPN 统一认证页面", error_code=WEBVPN_ERR_UPSTREAM_REDIRECT)

            hidden = self._extract_hidden_fields(page.text)
            key_b64 = self._extract_rsa_key_from_html(page.text) or _RSA_PUBLIC_KEY_B64
            post_url = self._extract_login_form_action(page.text, page.url)
            form_data = self._build_login_form(hidden, key_b64)
            response = self._submit_login_form(hidden, key_b64, post_url, form_data)
            self._raise_for_webvpn_response(response)

            challenge = self._extract_second_auth_form(response.text, response.url)
            logger.info(
                "WebVPN password submit: status=%s final=%s second_auth=%s",
                response.status_code,
                self._safe_url_metadata(response.url),
                bool(challenge),
            )
            if challenge:
                return self._create_webvpn_second_auth_flow(
                    challenge,
                    source="password",
                    remember=False,
                    target_service=target_service,
                )

            if self._is_webvpn_login_url(response.url):
                error = self._extract_error_message(response.text)
                error_type = _classify_login_error(error)
                raise NEULoginError(
                    _public_login_error_message(error, error_type),
                    error_type,
                )

            self._sync_cas_cookie_to_webvpn({})
            target = self._verify_webvpn_login_target(target_service)
            if not target["authenticated"]:
                message = (
                    "账号认证完成，但未能建立选课系统的 WebVPN 会话"
                    if target_service == "jwxk"
                    else "账号认证完成，但未能建立教务系统会话"
                )
                raise WebVPNLoginError(message, error_code=WEBVPN_ERR_SESSION_ESTABLISH)
            self._logged_in = True
            self._save_cookies()
            return {
                "status": "authenticated", "username": self.username or None,
                "target_service": target_service,
                "service_auth_state": target.get("service_auth_state", "authenticated"),
            }
        except requests.exceptions.Timeout as error:
            raise WebVPNLoginError("WebVPN 登录请求超时，请检查网络或改用微信扫码快速登录", error_code=WEBVPN_ERR_UPSTREAM_TIMEOUT) from error
        except requests.RequestException as error:
            raise WebVPNLoginError(
                f"WebVPN 请求失败（{type(error).__name__}）",
                error_code=WEBVPN_ERR_UNKNOWN,
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
            self._raise_for_webvpn_response(page)
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

    def refresh_webvpn_captcha(self, flow_id: str) -> Dict[str, Any]:
        flow = self._get_webvpn_sms_flow(flow_id)
        if flow.get("sms_verified"):
            return self._webvpn_session_pending(flow)
        result = self._fetch_webvpn_captcha(flow)
        expires_in = self._renew_webvpn_sms_window(flow)
        return {
            "status": "captcha_refreshed",
            "expires_in": expires_in,
            **result,
        }

    def get_webvpn_sms_challenge(self) -> Optional[Dict[str, Any]]:
        """Return the safe, user-facing view of the current SMS challenge."""
        flow = self._webvpn_sms_flow
        if not flow:
            return None
        flow = self._get_webvpn_sms_flow(str(flow.get("id") or ""))
        image = str(flow.get("captcha_image") or "")
        media_type = str(flow.get("captcha_media_type") or "image/jpeg")
        return {
            "status": "sms_required",
            "flow_id": flow["id"],
            "captcha_image": f"data:{media_type};base64,{image}" if image else "",
            "target_service": str(flow.get("target_service") or "primary"),
            "sms_verified": bool(flow.get("sms_verified")),
            "expires_in": max(
                0,
                int(float(flow.get("code_expires_at") or flow["expires_at"]) - time.time()),
            ),
        }

    @staticmethod
    def _is_graphic_captcha_error(code: Any, message: str) -> bool:
        if str(code).lower() in {"codeerr", "captcha_error", "captcha_invalid"}:
            return True
        # Explicit SMS errors must not discard the image or require a new SMS.
        return "短信" not in message and any(
            marker in message for marker in ("图形验证码", "图片验证码", "验证码错误", "验证码不正确", "校验码")
        ) and any(
            marker in message for marker in ("错误", "不正确", "失败", "失效", "过期", "不匹配", "为空")
        )

    def _webvpn_captcha_rejected(self, flow_id: str, message: str = "") -> Dict[str, Any]:
        flow = self._get_webvpn_sms_flow(flow_id)
        flow["captcha_code"] = ""
        try:
            refreshed = self.refresh_webvpn_captcha(flow_id)
        except WebVPNLoginError as error:
            if error.error_code not in {WEBVPN_ERR_CAPTCHA_FETCH, WEBVPN_ERR_UPSTREAM_TIMEOUT}:
                raise
            flow["captcha_image"] = ""
            refreshed = {"captcha_image": "", "captcha_refresh_failed": True}
            message = "图形验证码不正确，且新图片加载失败，请点击刷新图片后重新填写"
        return {
            **refreshed,
            # Refresh reports captcha_refreshed, but the operation still failed.
            # Put the error envelope last so it cannot be overwritten.
            "success": False,
            "status": "captcha_invalid",
            "captcha_invalid": True,
            "error_code": WEBVPN_ERR_CAPTCHA_INVALID,
            "message": message or "图形验证码不正确，请填写新图片后重新获取短信验证码",
        }

    def send_webvpn_sms_code(self, flow_id: str, captcha_code: str) -> Dict[str, Any]:
        """Ask the official second-auth endpoint to send the SMS code."""
        flow = self._get_webvpn_sms_flow(flow_id)
        if flow.get("sms_verified"):
            return self._webvpn_session_pending(flow)
        captcha_code = str(captcha_code or "").strip()
        if not captcha_code or len(captcha_code) > 16:
            raise WebVPNLoginError("请输入图形验证码", error_code=WEBVPN_ERR_CAPTCHA_INVALID)
        flow["captcha_code"] = captcha_code
        try:
            endpoint = urljoin(flow["page_url"], "secondAuthCode")
            # The official WebVPN AJAX URL parser uses this exact marker for
            # HTTPS requests to pass.neu.edu.cn. It is required by the gateway
            # even though the browser-visible path is already proxied.
            if flow.get("network_mode") == "webvpn":
                parsed = urlsplit(endpoint)
                if not re.search(r"(?:^|[?&])vpn-12-o[12]-", parsed.query):
                    endpoint = _prepend_query(
                        endpoint,
                        f"vpn-12-o2-{_WEBVPN_CAS_HOST}",
                    )
            response = self._session.post(
                endpoint,
                data={"code": captcha_code, "method": "mobile"},
                timeout=self.timeout,
                verify=self.verify_ssl,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Referer": flow["page_url"],
                },
            )
            self._raise_for_webvpn_response(response)
            try:
                result = response.json()
                non_json = False
            except (ValueError, AttributeError):
                result = {"message": response.text[:200]}
                non_json = True
            if not isinstance(result, dict):
                result = {"message": "短信接口返回了非对象响应"}
                non_json = True
        except requests.RequestException as error:
            code = WEBVPN_ERR_UPSTREAM_TIMEOUT if isinstance(error, requests.Timeout) else WEBVPN_ERR_UNKNOWN
            raise WebVPNLoginError(
                f"发送短信验证码失败（{type(error).__name__}）",
                error_code=code,
            ) from error

        info = next(
            (result[key] for key in ("info", "code", "status") if key in result and result[key] is not None),
            None,
        )
        message = str(result.get("message") or result.get("msg") or "")
        logger.info(
            "WebVPN SMS send response: status=%s result=%s keys=%s",
            response.status_code,
            info,
            sorted(result.keys()),
        )
        if non_json:
            raise WebVPNLoginError(
                "短信接口返回了无法解析的响应，请重新开始登录",
                error_code=WEBVPN_ERR_UPSTREAM_NON_JSON,
            )
        if self._is_graphic_captcha_error(info, message):
            return self._webvpn_captcha_rejected(flow_id, message)
        if info in {"send", "success", "ok", 0, "0"} or result.get("success") is True:
            return {
                "status": "sent",
                "expires_in": self._renew_webvpn_sms_window(flow),
            }
        if info == "max" or "频繁" in message:
            raise WebVPNLoginError("发送过于频繁，请稍后再试", error_code=WEBVPN_ERR_SMS_RATE_LIMITED)
        if info == "unknow":
            raise WebVPNLoginError("统一认证未绑定手机号码，无法进行短信验证", error_code=WEBVPN_ERR_SMS_UNBOUND)
        if str(info).lower() in {"timeout", "codeerror", "sms_error", "invalid"}:
            raise WebVPNLoginError(
                message or "短信验证码发送失败",
                error_code=WEBVPN_ERR_SMS_INVALID,
            )
        raise WebVPNLoginError(message or "短信验证码发送失败", error_code=WEBVPN_ERR_UNKNOWN)

    def verify_webvpn_sms_code(self, flow_id: str, code: str, trust_device: bool = False) -> Dict[str, Any]:
        """Submit the official second-auth form and verify the new session."""
        flow = self._get_webvpn_sms_flow(flow_id)
        if flow.get("sms_verified"):
            return self._complete_webvpn_sms_login(flow)
        if not str(code or "").strip():
            raise WebVPNLoginError("请输入短信验证码", error_code=WEBVPN_ERR_SMS_INVALID)
        try:
            data = dict(flow.get("hidden_fields") or {})
            data.update({
                "imgCode": flow.get("captcha_code", ""),
                "scendAuthCode": str(code or "").strip(),
                "method": data.get("method") or "mobile",
                "_eventId": data.get("_eventId") or "submit",
            })
            response = self._session.post(
                flow["form_action"],
                data=data,
                timeout=self.timeout,
                verify=self.verify_ssl,
                headers={"Referer": flow["page_url"]},
            )
            self._raise_for_webvpn_response(response)
        except requests.RequestException as error:
            code = WEBVPN_ERR_UPSTREAM_TIMEOUT if isinstance(error, requests.Timeout) else WEBVPN_ERR_UNKNOWN
            raise WebVPNLoginError(
                f"验证短信验证码失败（{type(error).__name__}）",
                error_code=code,
            ) from error

        try:
            response_json = response.json()
        except (ValueError, AttributeError):
            response_json = {}
        response_code = (
            str(next(
                (response_json[key] for key in ("code", "info", "status") if key in response_json and response_json[key] is not None),
                "",
            )).lower()
            if isinstance(response_json, dict) else ""
        )
        response_message = str(response_json.get("message") or response_json.get("msg") or "") if isinstance(response_json, dict) else ""
        if self._is_graphic_captcha_error(response_code, response_message):
            return self._webvpn_captcha_rejected(flow_id, response_message)
        if response_code in {"timeout", "codeerror", "sms_error", "invalid"}:
            raise WebVPNLoginError(response_message or "短信验证码不正确或已过期，请核对后重试", error_code=WEBVPN_ERR_SMS_INVALID)
        if self._is_webvpn_login_url(response.url) or self._extract_second_auth_form(response.text, response.url):
            error = self._extract_error_message(response.text)
            if self._is_graphic_captcha_error("", error):
                return self._webvpn_captcha_rejected(flow_id, error)
            raise WebVPNLoginError(error or "短信验证码验证失败", error_code=WEBVPN_ERR_SMS_INVALID)

        flow["sms_verified"] = True
        flow["expires_at"] = time.time() + WEBVPN_SMS_VALIDITY_SECONDS
        flow.pop("code_expires_at", None)
        flow.pop("hidden_fields", None)
        flow.pop("captcha_code", None)
        flow.pop("captcha_image", None)
        return self._complete_webvpn_sms_login(flow)

    @staticmethod
    def _webvpn_session_pending(flow: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "session_pending", "sms_verified": True,
            "flow_id": flow["id"],
            "target_service": str(flow.get("target_service") or "primary"),
            "error_code": WEBVPN_ERR_SESSION_ESTABLISH,
            "expires_in": max(0, int(flow["expires_at"] - time.time())),
            "message": "短信验证已通过，但教务会话暂未建立；请点击“继续建立会话”，无需重新获取或提交短信验证码。",
        }

    def _complete_webvpn_sms_login(self, flow: Dict[str, Any]) -> Dict[str, Any]:
        """Retry only session establishment after the one-time form succeeded."""
        target_service = str(flow.get("target_service") or "primary")
        try:
            self._sync_cas_cookie_to_webvpn({})
            if flow.get("source") == "qr":
                self._verify_service_qr_identity(target_service)
            target = self._verify_webvpn_login_target(target_service)
            if not target["authenticated"]:
                return self._webvpn_session_pending(flow)
        except (requests.RequestException, NEULoginError) as error:
            if getattr(error, "error_code", "") in {
                WEBVPN_ERR_CAMPUS_NETWORK, "WEBVPN_ACCOUNT_MISMATCH", "WEBVPN_ACCOUNT_UNVERIFIED",
            }:
                self._webvpn_sms_flow = None
                raise
            logger.info("WebVPN verified SMS session establishment pending error=%s", type(error).__name__)
            return self._webvpn_session_pending(flow)

        self._webvpn_sms_flow = None
        self._logged_in = True
        self._save_cookies()
        return {
            "status": "authenticated", "username": self.username or None,
            "message": "登录成功", "target_service": target_service,
            "service_auth_state": target.get("service_auth_state", "authenticated"),
        }

    def _verify_service_qr_identity(self, target_service: str) -> None:
        """A QR username hint is not proof of which account scanned the code."""
        if target_service not in SERVICE_CONFIGS:
            return
        expected = str(self.username or "")
        # Use the existing authenticated identity endpoint, not HTML labels or
        # the caller-supplied QR hint. This does not replace the active client.
        self.username = ""
        if not self._webvpn_health_check() or not self.username:
            self.username = expected
            raise WebVPNLoginError(
                "扫码已完成，但暂时无法核验扫码账号归属；当前登录未改变，请改用账号密码恢复",
                error_code="WEBVPN_ACCOUNT_UNVERIFIED",
            )
        if not expected or str(self.username) != expected:
            raise WebVPNLoginError(
                "扫码账号与当前登录账号不一致，当前登录未改变，请使用同一账号重新认证",
                error_code="WEBVPN_ACCOUNT_MISMATCH",
            )

    def _verify_webvpn_login_target(
        self, target_service: str, diagnostics: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Verify the service the foreground flow was opened for.

        JWXK is a sibling CAS application.  A valid gateway login may reach an
        official eligibility notice instead of JWXT's ``currentUser`` API;
        that is a successful WebVPN identity with a denied JWXK business
        scope, not a failed login.
        """
        if target_service not in SERVICE_CONFIGS:
            return {"authenticated": self._establish_webvpn_primary_session(diagnostics)}
        config = SERVICE_CONFIGS[target_service]
        if not config.get("token_cookie"):
            try:
                self.ensure_service_session(
                    target_service, network_mode_override="webvpn",
                    allow_identity_recovery=False,
                )
                response = self._request_service_redirects(
                    "GET", self._service_route_url(
                        config["origin"] + config["probe_path"], "webvpn",
                    ),
                    service_config=config, network_mode="webvpn",
                    timeout=self.timeout, verify=self.verify_ssl,
                )
                try:
                    usable = (
                        response.status_code == 200
                        and self._is_service_destination(response.url, config, "webvpn")
                        and not self._is_service_auth_required(response, config, "webvpn")
                    )
                finally:
                    self._close_response_safely(response)
                return {
                    "authenticated": True,
                    "service_auth_state": "authenticated" if usable else "service_unavailable",
                }
            except (NEULoginError, requests.RequestException) as error:
                if getattr(error, "error_code", None) == WEBVPN_ERR_CAMPUS_NETWORK:
                    raise
                return {"authenticated": True, "service_auth_state": "service_unavailable"}
        callback = self._service_route_url(config["service"], "webvpn")
        try:
            response = self._request_service_redirects(
                "GET", callback, service_config=config, network_mode="webvpn",
                timeout=self.timeout, verify=self.verify_ssl,
            )
            access_error = self._service_access_error(response, config, "jwxk")
            service_token = None if access_error is not None else self.get_service_token(
                "jwxk", network_mode="webvpn", request_path="/xsxk/web/now",
            )
            # This method runs only after the CAS/second-auth callback has
            # completed.  JWXK availability is a separate service state: an
            # outage or a public profile without a token must not turn a
            # successfully submitted SMS code back into "login failed".
            authenticated = True
            if diagnostics is not None:
                diagnostics["target_service"] = {
                    "service": "jwxk",
                    "status_code": response.status_code,
                    "final": self._safe_url_metadata(response.url),
                    "access_state": (
                        getattr(access_error, "error_code", "") if access_error else "authenticated"
                    ),
                }
            self._close_response_safely(response)
            return {
                "authenticated": authenticated,
                "service_auth_state": (
                    "not_in_selection_round" if access_error
                    else "authenticated" if service_token
                    else "service_unavailable"
                ),
            }
        except NEULoginError as error:
            if getattr(error, "error_code", None) == WEBVPN_ERR_CAMPUS_NETWORK:
                raise
            return {"authenticated": True, "service_auth_state": "service_unavailable"}
        except requests.RequestException:
            return {"authenticated": True, "service_auth_state": "service_unavailable"}

    def adopt_webvpn_gateway_session(self, candidate: "NEUAuthClient") -> None:
        """Merge a foreground service gateway login into an existing identity."""
        if candidate is self:
            self._save_cookies()
            return
        current_account = str(self.username or "")
        candidate_account = str(candidate.username or current_account)
        if current_account and candidate_account and current_account != candidate_account:
            raise NEULoginError("WebVPN 登录账号与当前账号不一致")
        for cookie in list(self._session.cookies):
            if cookie.domain.lstrip(".") == "webvpn.neu.edu.cn":
                try:
                    self._session.cookies.clear(cookie.domain, cookie.path, cookie.name)
                except KeyError:
                    pass
        for cookie in candidate.session.cookies:
            if cookie.domain.lstrip(".") == "webvpn.neu.edu.cn":
                self._session.cookies.set_cookie(cookie)
        self._service_token_cache.update({
            key: value for key, value in candidate._service_token_cache.items()
            if key[1] == "webvpn"
        })
        if candidate.password and not self.password:
            self.password = candidate.password
        self._save_cookies()

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
        # The gateway can reject even the cookie-bridge request when the
        # client is on the campus network. Keep the same stable routing
        # error as the login/page requests instead of leaking a generic 403.
        self._raise_for_webvpn_response(response)
        diagnostics["cookie_bridge"] = {
            "attempted": True,
            "status_code": response.status_code,
            "response_is_json": response.headers.get("Content-Type", "").startswith("application/json"),
            "set_cookies": self._safe_set_cookie_names(response),
        }

    def _establish_webvpn_primary_session(self, diagnostics: Optional[Dict[str, Any]] = None) -> bool:
        if self._webvpn_health_check(diagnostics):
            return True
        # The gateway identity and JWXT session are different. Bootstrap the
        # service's CAS callback with GET, then repeat only the read-only probe.
        try:
            response = self._request_service_redirects(
                "GET",
                WebVPNUrlCodec.convert_url("https://jwxt.neu.edu.cn/jwapp/sys/homeapp/index.do"),
                service_config={"host": "jwxt.neu.edu.cn"}, network_mode="webvpn",
                timeout=min(self.timeout, 8), verify=self.verify_ssl,
            )
            try:
                response.raise_for_status()
            finally:
                self._close_response_safely(response)
        except requests.RequestException as error:
            # The gateway may have established the session before its page
            # response timed out. The identity endpoint remains authoritative.
            logger.info("WebVPN primary CAS bootstrap request failed error=%s", type(error).__name__)
        except NEULoginError as error:
            if getattr(error, "error_code", "") == WEBVPN_ERR_CAMPUS_NETWORK:
                raise
            logger.info("WebVPN primary CAS bootstrap unavailable error=%s", type(error).__name__)
            return False
        return self._webvpn_health_check(diagnostics)

    def _webvpn_health_check(self, diagnostics: Optional[Dict[str, Any]] = None) -> bool:
        health_url = "https://jwxt.neu.edu.cn/jwapp/sys/homeapp/api/home/currentUser.do"
        response = None
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
                diagnostics["health"]["response_code"] = data.get("code") if isinstance(data, dict) else None
            if response.status_code != 200 or not isinstance(data, dict) or str(data.get("code")) != "0":
                logger.info("WebVPN primary identity probe rejected status=%s", response.status_code)
                return False
            user_data = data.get("datas", {})
            account = user_data.get("userId") if isinstance(user_data, dict) else None
            if not isinstance(account, str) or not account.strip():
                return False
            self.username = account.strip()
            return True
        except (requests.RequestException, ValueError) as error:
            logger.info("WebVPN primary identity probe unavailable error=%s", type(error).__name__)
            return False
        finally:
            if response is not None:
                self._close_response_safely(response)

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
            pending_flow = self._webvpn_sms_flow
            if pending_flow:
                if time.time() < float(pending_flow.get("expires_at", 0)):
                    # Interactive second authentication owns this Session now.
                    # Background readers must wait for the foreground instead
                    # of restarting password login and replacing the CAPTCHA.
                    self._logged_in = False
                    return False
                self._webvpn_sms_flow = None
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
                if getattr(error, "error_code", None) == WEBVPN_ERR_CAMPUS_NETWORK:
                    raise
                logger.warning(
                    "WebVPN 账号密码静默恢复失败，错误类型: %s",
                    getattr(error, "error_type", LOGIN_ERR_UNKNOWN),
                )
                return False

            if result.get("status") == "authenticated":
                self._logged_in = True
                return True

            # 短信验证属于交互式认证，静默恢复不能擅自发送短信；保留
            # challenge 供前台弹窗接管，避免后台反复重试制造请求风暴。
            if result.get("status") == "sms_required":
                logger.info("WebVPN 静默恢复需要短信验证，等待前台确认")
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
            with self._auth_operation_lock:
                with remote_read_bypass():
                    recovered = self._logged_in or self.ensure_login()
            if not recovered:
                if self.active_mode == "webvpn":
                    raise WebVPNRequiredError("WebVPN 会话无效，请重新扫码登录")
                raise NEULoginError("未登录或登录已过期")
        
        # 添加默认超时
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout
        
        recovery_generation = self._auth_recovery_generation
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
            with self._auth_operation_lock:
                # Another concurrent read may already have repaired the same
                # client while this response was in flight.
                if (
                    self._logged_in
                    and self._auth_recovery_generation != recovery_generation
                ):
                    recovered = True
                else:
                    self._logged_in = False
                    with remote_read_bypass():
                        recovered = self.ensure_login()
                    if recovered:
                        self._auth_recovery_generation += 1
            if not recovered:
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

    @staticmethod
    def _close_response_safely(response: requests.Response) -> None:
        try:
            response.close()
        except AttributeError:
            # Lightweight response doubles and malformed transports may not
            # expose a usable raw stream. Authentication recovery must still
            # preserve the original result instead of failing during cleanup.
            pass

    def request_service(
        self,
        service: str,
        method: str,
        path: str,
        *,
        network_mode_override: Optional[str] = None,
        retry_on_auth: bool = True,
        allow_identity_recovery: bool = True,
        **kwargs,
    ) -> requests.Response:
        """Request one explicitly supported campus service on the shared session.

        Every service reuses this client's single requests.Session and CAS
        identity.  A service may choose its own direct/WebVPN route without
        mutating the primary JWXT ``active_mode``.  A missing business-system
        session is established once and the original request is retried once.
        """
        config = SERVICE_CONFIGS.get(service)
        if config is None:
            raise ValueError(f"unsupported service: {service}")
        parsed = urlparse(path)
        if parsed.scheme or parsed.netloc or not path.startswith("/"):
            raise ValueError("service path must be an absolute relative path")
        normalized_path = parsed.path or "/"
        if not any(normalized_path.startswith(prefix) for prefix in config["allowed_prefixes"]):
            raise ValueError("service path is not allowed")
        network_mode = self._service_network_mode(config, network_mode_override)
        usable_service_token = False
        if not self._logged_in and config.get("token_cookie"):
            usable_service_token = bool(self.get_service_token(
                service,
                network_mode=network_mode,
                request_path=normalized_path,
            ))
        if not self._logged_in and not usable_service_token:
            with remote_read_bypass():
                recovered = self.ensure_login()
            if (
                not recovered
                and network_mode == "direct"
                and self.username and self.password
            ):
                recovered = self._login_direct_service(config)
            if not recovered:
                raise NEULoginError("未登录或登录已过期")
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout
        if "verify" not in kwargs:
            kwargs["verify"] = self.verify_ssl
        url = self._service_route_url(urljoin(config["origin"], path), network_mode)
        request_options = self._service_request_options(
            service, config, kwargs,
            network_mode=network_mode, request_path=normalized_path,
        )
        response = self._request_service_redirects(
            method, url, service_config=config, network_mode=network_mode, **request_options
        )
        access_error = self._service_access_error(response, config, service)
        if access_error is not None:
            self._close_response_safely(response)
            raise access_error
        if self._is_service_auth_required(
            response, config, network_mode, request_path=normalized_path,
        ) and retry_on_auth:
            first_rejection = self._service_auth_response_kind(response)
            logger.warning(
                "service session rejected service=%s path=%s response=%s; rebuilding child session",
                service, normalized_path, first_rejection,
            )
            self._close_response_safely(response)
            self.ensure_service_session(
                service, network_mode_override=network_mode_override,
                force_refresh=True,
                allow_identity_recovery=allow_identity_recovery,
            )
            request_options = self._service_request_options(
                service, config, kwargs,
                network_mode=network_mode, request_path=normalized_path,
            )
            response = self._request_service_redirects(
                method, url, service_config=config, network_mode=network_mode, **request_options
            )
            access_error = self._service_access_error(response, config, service)
            if access_error is not None:
                self._close_response_safely(response)
                raise access_error
        if self._is_service_auth_required(
            response, config, network_mode, request_path=normalized_path,
        ):
            final_rejection = self._service_auth_response_kind(response)
            self._close_response_safely(response)
            raise NEULoginError(
                f"业务系统接口 {normalized_path} 拒绝当前会话（{final_rejection}）"
            )
        if not self._is_service_destination(response.url, config, network_mode):
            self._close_response_safely(response)
            raise NEULoginError("业务系统返回了不受信任的跳转地址")
        return response

    @staticmethod
    def _service_access_error(
        response: requests.Response, config: Dict[str, Any], service: str,
    ) -> Optional[ServiceAccessError]:
        rules = config.get("access_denied_markers") or ()
        if not rules:
            return None
        try:
            body = re.sub(r"\s+", "", str(response.text or ""))
        except (AttributeError, TypeError, ValueError):
            return None
        for rule in rules:
            markers = tuple(re.sub(r"\s+", "", str(item)) for item in rule.get("markers") or ())
            if markers and all(marker in body for marker in markers):
                return ServiceAccessError(
                    str(rule.get("message") or "当前账号不能访问该业务系统"),
                    service=service,
                    error_code=str(rule.get("error_code") or "SERVICE_ACCESS_DENIED"),
                )
        return None

    @staticmethod
    def _service_auth_response_kind(response: requests.Response) -> str:
        """Return a non-sensitive reason label for an auth-shaped response."""
        content_type = str(response.headers.get("Content-Type", "")).casefold()
        if "json" in content_type:
            try:
                payload = response.json()
                code = str(payload.get("code") or response.status_code or "unknown")
                raw_message = str(payload.get("msg") or payload.get("message") or "").strip()
                safe_message = re.sub(r"https?://\S+", "[链接已隐藏]", raw_message)
                safe_message = re.sub(
                    r"(?i)\b(token|ticket|cookie|authorization)\b\s*[:=：]\s*\S+",
                    r"\1=[已隐藏]", safe_message,
                )
                safe_message = re.sub(r"\b\d{6,}\b", "[编号已隐藏]", safe_message)
                safe_message = re.sub(
                    r"[A-Za-z0-9_-]{20,}(?:\.[A-Za-z0-9_-]{20,}){1,2}",
                    "[令牌已隐藏]", safe_message,
                )
                safe_message = safe_message[:120]
                return (
                    f"JSON 业务响应 code={code}，提示={safe_message}"
                    if safe_message else f"JSON 业务响应 code={code}，无提示文本"
                )
            except (AttributeError, TypeError, ValueError):
                return f"无法解析的 JSON 响应 HTTP {response.status_code}"
        try:
            preview = str(response.text or "").lstrip().casefold()[:128]
        except (AttributeError, TypeError, ValueError):
            preview = ""
        if "html" in content_type or preview.startswith(("<!doctype html", "<html")):
            return "返回登录 HTML"
        parsed = urlparse(str(getattr(response, "url", "") or ""))
        if parsed.hostname:
            return f"跳转至 {parsed.hostname}{parsed.path}"
        return f"HTTP {response.status_code}"

    def get_service_token(
        self,
        service: str,
        *,
        network_mode: Optional[str] = None,
        request_path: str = "",
    ) -> Optional[str]:
        """Return a service bearer token kept inside the shared cookie jar."""
        config = SERVICE_CONFIGS.get(service)
        if config is None:
            raise ValueError(f"unsupported service: {service}")
        cookie_name = config.get("token_cookie")
        if not cookie_name:
            return None
        allowed_domains = (
            {"webvpn.neu.edu.cn"}
            if network_mode == "webvpn"
            else {config["host"]}
            if network_mode == "direct"
            else {config["host"], "webvpn.neu.edu.cn"}
        )
        def path_matches(cookie_path: str) -> bool:
            if not request_path:
                return True
            normalized_cookie_path = str(cookie_path or "/").rstrip("/") or "/"
            return (
                normalized_cookie_path == "/"
                or request_path == normalized_cookie_path
                or request_path.startswith(normalized_cookie_path + "/")
            )
        candidates = [
            cookie for cookie in self._session.cookies
            if cookie.name == cookie_name
            and cookie.domain.lstrip(".") in allowed_domains
            and (network_mode == "webvpn" or path_matches(cookie.path))
        ]
        if candidates:
            candidates.sort(key=lambda cookie: len(cookie.path or ""), reverse=True)
            return str(candidates[0].value or "") or None
        if network_mode == "webvpn":
            cache_key = (service, network_mode)
            cached = self._service_token_cache.get(cache_key)
            if cached:
                return cached
            token = self._get_webvpn_virtual_cookie(
                config["host"], request_path or "/", cookie_name,
            )
            if token:
                self._service_token_cache[cache_key] = token
            return token
        return None

    def _get_webvpn_virtual_cookie(
        self, host: str, request_path: str, cookie_name: str,
    ) -> Optional[str]:
        """Read an upstream cookie from WebVPN's virtual cookie store."""
        response = self._session.get(
            f"{WEBVPN_ORIGIN}/wengine-vpn/cookie",
            params={
                "method": "get",
                "host": host,
                "scheme": "https",
                "path": request_path or "/",
                "vpn_timestamp": str(int(time.time() * 1000)),
            },
            timeout=self.timeout,
            verify=self.verify_ssl,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self._raise_for_webvpn_response(response)
        cookies = SimpleCookie()
        try:
            cookies.load(str(response.text or ""))
        except Exception:
            return None
        morsel = cookies.get(cookie_name)
        if morsel is None:
            return None
        return str(morsel.value or "") or None

    def _service_request_options(
        self, service: str, config: Dict[str, Any], kwargs: Dict[str, Any],
        *, network_mode: str, request_path: str,
    ) -> Dict[str, Any]:
        options = dict(kwargs)
        token_header = config.get("token_header")
        token = self.get_service_token(
            service, network_mode=network_mode, request_path=request_path,
        ) if token_header else None
        if token:
            headers = dict(options.get("headers") or {})
            headers.setdefault(token_header, token)
            options["headers"] = headers
        return options

    def _clear_service_token(self, service: str, *, network_mode: str) -> None:
        config = SERVICE_CONFIGS.get(service)
        if config is None or not config.get("token_cookie"):
            return
        self._service_token_cache.pop((service, network_mode), None)
        domain = "webvpn.neu.edu.cn" if network_mode == "webvpn" else config["host"]
        matches = [
            (cookie.domain, cookie.path, cookie.name)
            for cookie in self._session.cookies
            if cookie.name == config["token_cookie"]
            and cookie.domain.lstrip(".") == domain
        ]
        for cookie_domain, cookie_path, cookie_name in matches:
            try:
                self._session.cookies.clear(
                    domain=cookie_domain, path=cookie_path, name=cookie_name,
                )
            except KeyError:
                pass
        if network_mode == "webvpn":
            try:
                response = self._session.post(
                    f"{WEBVPN_ORIGIN}/wengine-vpn/cookie",
                    params={
                        "method": "set",
                        "host": config["host"],
                        "scheme": "https",
                        "path": "/xsxk/auth/cas",
                        "ck_data": (
                            f"{config['token_cookie']}=; Max-Age=0; Path=/"
                        ),
                    },
                    timeout=self.timeout,
                    verify=self.verify_ssl,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
                self._raise_for_webvpn_response(response)
            except requests.RequestException as error:
                logger.warning(
                    "failed to clear WebVPN virtual service token service=%s error=%s",
                    service,
                    type(error).__name__,
                )

    def _login_direct_service(self, config: Dict[str, Any]) -> bool:
        original_target = self.target
        original_mode = self.active_mode
        original_logged_in = self._logged_in
        try:
            return bool(self._do_login(config["service"]))
        finally:
            self.target = original_target
            self.active_mode = original_mode
            if original_logged_in:
                self._logged_in = True

    def _login_webvpn_service_identity(self, *, target_service: str = "primary") -> Dict[str, Any]:
        """Authenticate WebVPN without changing a direct primary login.

        A user may keep JWXT on the direct route while selecting WebVPN only
        for JWXK.  The official WebVPN password flow temporarily needs
        ``active_mode=webvpn`` and may clear its cookie jar while recovering
        from an unexpected gateway page.  Preserve the non-WebVPN cookies and
        primary route, then merge the newly issued gateway cookies back into
        the one shared Session.
        """
        original_mode = self.active_mode
        original_logged_in = self._logged_in
        preserved = [
            cookie
            for cookie in self._session.cookies.copy()
            if cookie.domain.lstrip(".") != "webvpn.neu.edu.cn"
        ]
        try:
            return self.start_webvpn_password_login(target_service=target_service)
        finally:
            self.active_mode = original_mode
            for cookie in preserved:
                self._session.cookies.set_cookie(cookie)
            if original_logged_in:
                self._logged_in = True
            # start_webvpn_password_login persists while its temporary mode is
            # active.  Persist once more after restoring the primary route.
            self._save_cookies()

    def ensure_service_session(
        self, service: str, *, network_mode_override: Optional[str] = None,
        force_refresh: bool = False, allow_identity_recovery: bool = True,
    ) -> bool:
        """Establish one business-system session from the shared CAS identity."""
        config = SERVICE_CONFIGS.get(service)
        if config is None:
            raise ValueError(f"unsupported service: {service}")
        network_mode = self._service_network_mode(config, network_mode_override)
        token_probe_path = "/xsxk/web/now" if service == "jwxk" else "/"
        if (
            config.get("token_cookie")
            and not force_refresh
            and self.get_service_token(
                service, network_mode=network_mode,
                request_path=token_probe_path,
            )
        ):
            return True
        service_callback = self._service_route_url(config["service"], network_mode)

        def establish() -> bool:
            # Start from the service provider's own CAS endpoint.  JWXK uses
            # this visit to initialize its application session before sending
            # the browser to pass.neu.edu.cn and accepting the callback.
            ticket_response = self._request_service_redirects(
                "GET",
                service_callback,
                service_config=config,
                network_mode=network_mode,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            access_error = self._service_access_error(ticket_response, config, service)
            if access_error is not None:
                self._close_response_safely(ticket_response)
                raise access_error
            established = (
                self._is_service_destination(ticket_response.url, config, network_mode)
                and not self._is_service_auth_required(ticket_response, config, network_mode)
            )
            self._close_response_safely(ticket_response)
            return established

        def try_establish() -> bool:
            try:
                return establish()
            except NEULoginError as error:
                if isinstance(error, ServiceAccessError):
                    raise
                if network_mode != "webvpn":
                    raise
                if getattr(error, "error_code", None) == WEBVPN_ERR_CAMPUS_NETWORK:
                    raise
                # A direct primary identity commonly reaches a WebVPN login or
                # verification trampoline that is not a valid JWXK callback.
                # Treat that as a missing gateway identity so the saved-password
                # flow below actually runs; credentials are still submitted only
                # to the fixed official WebVPN entry and CAS form.
                logger.info(
                    "WebVPN service identity is not established service=%s error=%s",
                    service,
                    type(error).__name__,
                )
                return False

        # A rejected bearer token must not be accepted as evidence that the
        # CAS callback issued a fresh JWXK session.  Remove only this route's
        # service token; primary JWXT/CAS cookies remain untouched.
        if force_refresh:
            self._clear_service_token(service, network_mode=network_mode)
        established = try_establish()
        if established and config.get("token_cookie"):
            established = bool(self.get_service_token(
                service, network_mode=network_mode,
                request_path=token_probe_path,
            ))
        if not established:
            pending = self._webvpn_sms_flow or self._webvpn_qr_flow
            if pending and time.time() < float(pending.get("expires_at", 0)):
                raise NEULoginError("业务系统等待完成当前验证码或扫码认证")
            if not allow_identity_recovery:
                raise NEULoginError("业务系统会话需要重新认证")
            logger.info("跨系统 CAS 会话已过期，尝试恢复统一认证后重建业务会话...")
            cross_route_webvpn = (
                network_mode == "webvpn" and self.active_mode != "webvpn"
            )
            cross_route_direct = (
                network_mode == "direct" and self.active_mode != "direct"
            )
            webvpn_password_attempted = False
            identity_recovered = False

            # A working direct JWXT login says nothing about the gateway login.
            # When JWXK explicitly uses WebVPN, first use same-account saved
            # credentials to establish that gateway identity without replacing
            # or clearing the direct primary Session.
            if cross_route_webvpn and self.username and self.password:
                webvpn_password_attempted = True
                try:
                    webvpn_result = self._login_webvpn_service_identity(target_service=service)
                except NEULoginError as error:
                    if getattr(error, "error_code", None) == WEBVPN_ERR_CAMPUS_NETWORK:
                        raise
                    logger.info(
                        "WebVPN silent password login unavailable service=%s error=%s",
                        service,
                        type(error).__name__,
                    )
                    webvpn_result = {}
                if webvpn_result.get("status") == "authenticated":
                    identity_recovered = True
                    self._clear_service_token(service, network_mode=network_mode)
                    established = try_establish()
                elif webvpn_result.get("status") == "sms_required":
                    # Captcha/SMS protection is intentionally not completed in
                    # the background; keep the flow for the visible modal.
                    logger.info("WebVPN service recovery requires foreground CAPTCHA/SMS")

            primary_recovered = False
            if (
                not cross_route_webvpn and not cross_route_direct
                and not config.get("independent_identity_recovery")
            ):
                primary_recovered = self.ensure_login()
                identity_recovered = bool(primary_recovered)
            if primary_recovered:
                self._clear_service_token(service, network_mode=network_mode)
                established = try_establish()
            if established and config.get("token_cookie"):
                established = bool(self.get_service_token(
                    service, network_mode=network_mode,
                    request_path=token_probe_path,
                ))
            if (
                not established
                and network_mode == "direct"
                and self.username and self.password
            ):
                # The primary JWXT route may be unavailable while JWXK remains
                # directly reachable.  Re-authenticate CAS against this
                # service on the same Session without changing the primary
                # route or creating a second credential store.
                self._clear_service_token(service, network_mode=network_mode)
                direct_service_recovered = self._login_direct_service(config)
                identity_recovered = identity_recovered or bool(direct_service_recovered)
                established = direct_service_recovered
                if established and config.get("token_cookie"):
                    established = bool(self.get_service_token(
                        service, network_mode=network_mode,
                        request_path=token_probe_path,
                    ))
            if (
                not established
                and network_mode == "webvpn"
                and self.username and self.password
                and not webvpn_password_attempted
            ):
                try:
                    webvpn_result = self._login_webvpn_service_identity(target_service=service)
                except NEULoginError as error:
                    if getattr(error, "error_code", None) == WEBVPN_ERR_CAMPUS_NETWORK:
                        raise
                    webvpn_result = {}
                if webvpn_result.get("status") == "authenticated":
                    identity_recovered = True
                    self._clear_service_token(service, network_mode=network_mode)
                    established = try_establish()
                    if established and config.get("token_cookie"):
                        established = bool(self.get_service_token(
                            service, network_mode=network_mode,
                            request_path=token_probe_path,
                        ))
                elif webvpn_result.get("status") == "sms_required":
                    # Leave the challenge for the foreground authentication modal.
                    logger.info("WebVPN service recovery requires foreground CAPTCHA/SMS")
            if not established:
                if not identity_recovered:
                    raise NEULoginError("统一认证会话已过期")
                raise NEULoginError("统一认证恢复后仍无法建立业务系统会话")
        self._save_cookies()
        return True

    @staticmethod
    def _service_network_mode(config: Dict[str, Any], override: Optional[str]) -> str:
        mode = override or "direct"
        if mode not in {"direct", "webvpn"}:
            raise ValueError("service network mode must be direct or webvpn")
        if mode not in config["network_modes"]:
            raise ValueError("service does not support the requested network mode")
        return mode

    @staticmethod
    def _service_route_url(url: str, network_mode: str) -> str:
        return WebVPNUrlCodec.convert_url(url) if network_mode == "webvpn" else url

    @staticmethod
    def _webvpn_targets_host(url: str, hostname: str) -> bool:
        if urlparse(url).hostname != "webvpn.neu.edu.cn":
            return False
        try:
            WebVPNUrlCodec.restore_service_url(url, origin=f"https://{hostname}")
            return True
        except ValueError:
            return False

    def _is_service_destination(
        self, url: str, config: Dict[str, Any], network_mode: str
    ) -> bool:
        if network_mode == "webvpn":
            return self._webvpn_targets_host(url, config["host"])
        parsed = urlparse(str(url or ""))
        return (
            parsed.scheme == "https"
            and parsed.hostname == config["host"]
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
        )

    def _is_service_auth_required(
        self,
        response: requests.Response,
        config: Dict[str, Any],
        network_mode: str,
        *,
        request_path: str = "",
    ) -> bool:
        """Recognize CAS and a service's same-origin login trampoline."""
        response_url = str(getattr(response, "url", "") or "")
        if self._is_auth_redirect(response_url):
            return True
        markers = config.get("auth_html_markers", ())
        if markers and "html" in str(response.headers.get("Content-Type", "")).lower():
            preview = str(response.text or "")[:5000].lower()
            if all(marker.lower() in preview for marker in markers):
                return True
        response_codes = config.get("auth_response_codes") or ()
        if response_codes and "json" in str(response.headers.get("Content-Type", "")).lower():
            try:
                payload = response.json()
                if str(payload.get("code")) in response_codes:
                    markers = tuple(config.get("auth_response_message_markers") or ())
                    if not markers:
                        return True
                    message = str(payload.get("msg") or payload.get("message") or "").casefold()
                    exclusions = tuple(config.get("auth_response_message_exclusions") or ())
                    if any(str(marker).casefold() in message for marker in exclusions):
                        return False
                    if any(str(marker).casefold() in message for marker in markers):
                        return True
            except (AttributeError, TypeError, ValueError):
                pass
        expects_json = any(
            request_path.startswith(prefix)
            for prefix in config.get("json_prefixes") or ()
        )
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if expects_json and "json" not in content_type:
            try:
                preview = str(response.text or "").lstrip().lower()[:128]
            except (AttributeError, TypeError, ValueError):
                preview = ""
            if "html" in content_type or preview.startswith(("<!doctype html", "<html")):
                return True
        if network_mode == "webvpn":
            if self._webvpn_targets_host(response_url, "pass.neu.edu.cn"):
                return True
            return any(
                self._webvpn_targets_host(response_url, config["host"])
                and urlparse(response_url).path.endswith(path)
                for path in config["login_paths"]
            )
        parsed = urlparse(response_url)
        return parsed.hostname == config["host"] and parsed.path in config["login_paths"]

    def _request_service_redirects(
        self,
        method: str,
        url: str,
        *,
        service_config: Optional[Dict[str, Any]] = None,
        network_mode: str = "direct",
        **kwargs,
    ) -> requests.Response:
        """Follow a small trusted redirect chain without contacting other hosts."""
        options = dict(kwargs)
        options.pop("allow_redirects", None)
        current_method = method.upper()
        current_url = url
        for hop in range(9):
            parsed_current = urlparse(current_url)
            host = (parsed_current.hostname or "").lower()
            try:
                port = parsed_current.port
            except ValueError as exc:
                raise NEULoginError("业务系统返回了不受信任的跳转地址") from exc
            config = service_config or SERVICE_CONFIGS["cxcy"]
            allowed = False
            if network_mode == "webvpn":
                allowed = (
                    parsed_current.scheme == "https"
                    and host == "webvpn.neu.edu.cn"
                    and port in {None, 443}
                    and parsed_current.username is None
                    and parsed_current.password is None
                    and (
                        self._webvpn_targets_host(current_url, config["host"])
                        or self._webvpn_targets_host(current_url, "pass.neu.edu.cn")
                    )
                )
            else:
                allowed = (
                    parsed_current.scheme == "https"
                    and host in {config["host"], "pass.neu.edu.cn"}
                    and port in {None, 443}
                    and parsed_current.username is None
                    and parsed_current.password is None
                )
            if not allowed:
                raise NEULoginError("业务系统返回了不受信任的跳转地址")
            response = self._session.request(
                current_method, current_url, allow_redirects=False, **options
            )
            self._raise_if_webvpn_campus_block(response)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            location = response.headers.get("Location", "")
            if not location:
                return response
            current_url = urljoin(current_url, location)
            # JWXK's service-provider CAS endpoint currently emits an
            # absolute http://pass.neu.edu.cn/tpass/login redirect even when
            # entered over HTTPS.  Never send cookies or tickets over that
            # clear-text hop: recognize only this exact official CAS path and
            # upgrade it to HTTPS before the next request.  All other HTTP
            # redirects remain rejected by the allow-list on the next loop.
            parsed_next = urlparse(current_url)
            if (
                network_mode == "webvpn"
                and parsed_next.scheme == "https"
                and parsed_next.hostname == "webvpn.neu.edu.cn"
                and parsed_next.port in {None, 443}
                and parsed_next.username is None
                and parsed_next.password is None
                and parsed_next.path in {
                    urlparse(WebVPNUrlCodec.convert_url(
                        "http://pass.neu.edu.cn/tpass/login",
                    )).path,
                    urlparse(WebVPNUrlCodec.convert_url(
                        "http://pass.neu.edu.cn:80/tpass/login",
                    )).path,
                }
            ):
                # JWXK also emits an already-proxied HTTP CAS redirect.
                # Upgrade only this exact callback, preserving its query;
                # other HTTP proxy targets stay outside the allow-list.
                current_url = parsed_next._replace(
                    path=urlparse(WebVPNUrlCodec.convert_url(CAS_LOGIN_URL)).path,
                ).geturl()
                parsed_next = urlparse(current_url)
            if (
                parsed_next.scheme == "http"
                and parsed_next.hostname == "pass.neu.edu.cn"
                and parsed_next.port in {None, 80}
                and parsed_next.username is None
                and parsed_next.password is None
                and parsed_next.path == "/tpass/login"
            ):
                current_url = parsed_next._replace(
                    scheme="https", netloc="pass.neu.edu.cn",
                ).geturl()
                parsed_next = urlparse(current_url)
            # WebVPN service entry points may emit ordinary official CAS and
            # service callback URLs.  Keep the same trusted-host checks, then
            # route those next hops back through WebVPN before any request is
            # sent.  Without this bridge JWXK's http://pass CAS redirect is
            # upgraded safely but rejected on the following WebVPN hop.
            if (
                network_mode == "webvpn"
                and parsed_next.scheme == "https"
                and parsed_next.port in {None, 443}
                and parsed_next.username is None
                and parsed_next.password is None
                and parsed_next.hostname in {"pass.neu.edu.cn", config["host"]}
            ):
                current_url = WebVPNUrlCodec.convert_url(current_url)
            try:
                response.close()
            except AttributeError:
                # Lightweight response doubles may not have a transport body.
                pass
            if response.status_code in (301, 302, 303) and current_method != "HEAD":
                current_method = "GET"
                options.pop("data", None)
                options.pop("json", None)
                options.pop("params", None)
        raise requests.TooManyRedirects("campus service redirect limit exceeded")

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

    @property
    def timetable(self):
        """课表查询 API 入口。"""
        if self._timetable is None:
            from backend.core.timetable import TimetableAPI
            self._timetable = TimetableAPI(self)
        return self._timetable

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
    def _find_login_error_message(html: str) -> Optional[str]:
        """Return an explicit CAS error, without inventing an unknown one."""
        soup = BeautifulSoup(html, "lxml")
        for selector in ["#errormsg", ".error", "#errormsghide", ".alert"]:
            el = soup.select_one(selector)
            if el and el.get_text(strip=True):
                return el.get_text(strip=True)
        return None

    @staticmethod
    def _extract_error_message(html: str) -> str:
        """Extract a legacy display message for non-direct login flows."""
        return NEUAuthClient._find_login_error_message(html) or "未知错误"

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

    @staticmethod
    def _cookie_key(cookie) -> tuple[str, str, str]:
        return (
            str(getattr(cookie, "name", "") or ""),
            str(getattr(cookie, "domain", "") or ""),
            str(getattr(cookie, "path", "") or "/"),
        )

    def _isolated_read_session(self) -> tuple[requests.Session, dict[tuple[str, str, str], str | None]]:
        """Create a per-request Session snapshot for shared read traffic.

        ``requests.Session`` is not documented as thread-safe: its cookie jar,
        headers and redirect state are mutable.  A shallow copy of the
        connection adapters retains urllib3 pooling, while the cookie jar is
        copied so concurrent reads cannot overwrite one another.  Response
        cookies are merged back conditionally after the request.
        """
        source = self._session
        isolated = requests.Session()
        isolated.headers.update(dict(source.headers))
        isolated.params = dict(source.params)
        isolated.auth = source.auth
        isolated.proxies = dict(source.proxies)
        isolated.hooks = {name: list(values) for name, values in source.hooks.items()}
        isolated.verify = source.verify
        isolated.cert = source.cert
        isolated.trust_env = source.trust_env
        isolated.max_redirects = source.max_redirects
        # HTTPAdapter/PoolManager are designed for concurrent sends; sharing
        # the adapter preserves connection reuse without sharing Session state.
        isolated.adapters = dict(source.adapters)
        with self._session_state_lock:
            isolated.cookies = source.cookies.copy()
            snapshot = {
                self._cookie_key(cookie): str(cookie.value)
                for cookie in source.cookies
            }
        return isolated, snapshot

    def _merge_isolated_read_cookies(
        self,
        isolated: requests.Session,
        snapshot: dict[tuple[str, str, str], str | None],
    ) -> None:
        """Merge only cookie changes that are not stale relative to a snapshot."""
        with self._session_state_lock:
            current = {
                self._cookie_key(cookie): str(cookie.value)
                for cookie in self._session.cookies
            }
            for cookie in isolated.cookies:
                key = self._cookie_key(cookie)
                # A concurrent authentication/mutation may have replaced this
                # cookie.  Never let an older read response roll it back.
                if current.get(key) != snapshot.get(key):
                    continue
                self._session.cookies.set_cookie(cookie)

    def _request_on_session(
        self,
        session: requests.Session,
        method: str,
        url: str,
        **kwargs,
    ) -> requests.Response:
        return session.request(method, url, **kwargs)

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
        
        isolated: requests.Session | None = None
        cookie_snapshot: dict[tuple[str, str, str], str | None] = {}
        request_session = self._session
        if is_remote_read_context():
            isolated, cookie_snapshot = self._isolated_read_session()
            request_session = isolated

        try:
            response = self._request_on_session(request_session, method, url, **kwargs)
            self._raise_if_webvpn_campus_block(response)
            if isolated is not None:
                self._merge_isolated_read_cookies(isolated, cookie_snapshot)
            return response
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
            resp = self._request_on_session(request_session, method, alt_url, **kwargs)
            self._raise_if_webvpn_campus_block(resp)
            if isolated is not None:
                self._merge_isolated_read_cookies(isolated, cookie_snapshot)
            # 记住可用协议，后续请求直接使用
            self._protocol_override = "https://" if alt_url.startswith("https://") else "http://"
            logger.info(f"协议回退成功，后续请求将使用 {self._protocol_override}")
            return resp


# ── 异常 ──────────────────────────────────────────────────────────────────────
# NEULoginError 已在上方定义（class 需在 raise 之前先定义）
