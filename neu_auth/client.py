"""
neu_auth/client.py
==================
东北大学统一身份认证（CAS）登录客户端

特性：
- 自动重试机制
- 票据失效自动重新登录
- CAS Cookie 持久化（免密刷新票据）
- 请求限流保护
"""

import base64
import pickle
import re
import time
import logging
from functools import wraps
from typing import Optional, Callable, Dict, Any
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────

CAS_BASE_URL = "https://pass.neu.edu.cn/tpass"
CAS_LOGIN_URL = f"{CAS_BASE_URL}/login"

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
                except (requests.RequestException, NEULoginError) as e:
                    last_exception = e
                    logger.warning(f"请求失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        time.sleep(delay * (attempt + 1))  # 指数退避
            raise last_exception
        return wrapper
    return decorator


def _rsa_encrypt(username: str, password: str) -> str:
    """RSA加密"""
    der = base64.b64decode(_RSA_PUBLIC_KEY_B64)
    key = RSA.import_key(der)
    cipher = PKCS1_v1_5.new(key)
    plaintext = (username + password).encode("utf-8")
    encrypted = cipher.encrypt(plaintext)
    return base64.b64encode(encrypted).decode("utf-8")


# ── 主客户端 ──────────────────────────────────────────────────────────────────

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
        username: str,
        password: str,
        timeout: int = 15,
        verify_ssl: bool = True,
        cookie_file: Optional[str] = None,
    ):
        self.username = username
        self.password = password
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.target = "https://jwxt.neu.edu.cn"
        self.cookie_file = cookie_file  # Cookie 持久化文件路径

        self._session = requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)
        self._logged_in = False
        self._academic = None
        self._academic_report = None  # 学业监测报告 API
        
        # 尝试恢复之前的 session
        if cookie_file:
            self._load_cookies()

    @retry_on_error(max_retries=3, delay=2)
    def login(self, target: str = "https://jwxt.neu.edu.cn") -> bool:
        """
        执行 CAS 登录
        
        Args:
            target: 目标系统 URL
            
        Returns:
            登录是否成功
        """
        self.target = target
        service_url = self._resolve_service_url(target)
        logger.info("开始 CAS 登录...")

        # Step 1: 获取登录页
        login_page_url = f"{CAS_LOGIN_URL}?service={requests.utils.quote(service_url, safe='')}"
        resp = self._session.get(
            login_page_url,
            timeout=self.timeout,
            verify=self.verify_ssl,
            allow_redirects=True,
        )
        resp.raise_for_status()

        # 如果已登录（直接跳转到目标系统）
        if urlparse(resp.url).netloc != urlparse(CAS_LOGIN_URL).netloc:
            logger.info("已有有效会话")
            self._logged_in = True
            return True

        hidden = self._extract_hidden_fields(resp.text)

        # Step 2: 提交登录
        rsa_encrypted = _rsa_encrypt(self.username, self.password)
        form_data = {
            "un": self.username,
            "pd": self.password,
            "rsa": rsa_encrypted,
            "ul": str(len(self.username)),
            "pl": str(len(self.password)),
            "lt": hidden.get("lt", ""),
            "execution": hidden.get("execution", "e1s1"),
            "_eventId": "submit",
        }

        post_url = f"{CAS_LOGIN_URL}?service={requests.utils.quote(service_url, safe='')}"
        resp2 = self._session.post(
            post_url,
            data=form_data,
            timeout=self.timeout,
            verify=self.verify_ssl,
            allow_redirects=True,
        )
        resp2.raise_for_status()

        # 检查登录结果
        final_url = resp2.url
        if urlparse(final_url).netloc == urlparse(CAS_LOGIN_URL).netloc:
            error_msg = self._extract_error_message(resp2.text)
            raise NEULoginError(f"登录失败: {error_msg}")

        self._logged_in = True
        logger.info(f"登录成功，当前 URL: {final_url}")
        self._save_cookies()  # 保存登录后的 cookies
        return True

    def ensure_login(self) -> bool:
        """
        确保已登录
        
        登录恢复优先级：
        1. 检查当前 session 是否有效
        2. 尝试用 CAS Cookie 刷新票据（免密）
        3. 用账号密码重新登录
        
        Returns:
            是否成功登录
        """
        if self._logged_in:
            # 测试当前会话是否有效
            try:
                time.sleep(0.1)
                resp = self._session.get(
                    "https://jwxt.neu.edu.cn/jwapp/sys/homeapp/api/home/currentUser.do",
                    timeout=5,
                    allow_redirects=False
                )
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        if data.get("code") == "0":
                            return True
                    except:
                        pass
            except:
                pass
            
            # Session 失效，标记为未登录
            logger.info("业务系统 Session 失效，尝试恢复...")
            self._logged_in = False
        
        # 第2步：尝试用 CAS Cookie 刷新票据（免密）
        if self._try_refresh_ticket(self.target):
            return True
        
        # 第3步：用账号密码重新登录
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
            self.ensure_login()
        
        # 添加默认超时
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout
        
        # 发送请求
        resp = self._session.request(method, url, **kwargs)
        
        # 检查是否需要重新登录（302重定向到登录页）
        if resp.status_code == 302 and "pass.neu.edu.cn" in resp.headers.get("Location", ""):
            logger.info("检测到票据失效，重新登录...")
            self._logged_in = False
            self.ensure_login()
            # 重试原请求
            resp = self._session.request(method, url, **kwargs)
        
        return resp

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
            from neu_academic.api import AcademicAPI
            self._academic = AcademicAPI(self)
        return self._academic

    @property
    def academic_report(self):
        """学业监测报告 API 入口"""
        if self._academic_report is None:
            from neu_academic.report import AcademicReportAPI
            self._academic_report = AcademicReportAPI(self)
        return self._academic_report

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
            print(f"获取用户信息失败: {e}")
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
            file_info_url = f"http://jwxt.neu.edu.cn/jwapp/sys/emapcomponent/file/getUploadedAttachment/{avatar_token}.do"
            resp = self.get(file_info_url)
            print(f"[Avatar] 文件信息状态: {resp.status_code}")
            
            # 如果直接返回图片
            if resp.status_code == 200 and 'image' in resp.headers.get('Content-Type', ''):
                return resp.content
            
            # 尝试解析JSON获取实际文件URL
            try:
                data = resp.json()
                print(f"[Avatar] 文件信息: {data}")
                
                # 从 items 数组获取第一个文件的 fileUrl
                items = data.get('items', [])
                if items and len(items) > 0:
                    file_url = items[0].get('fileUrl')
                    if file_url:
                        # fileUrl 是相对路径，需要拼接域名
                        if file_url.startswith('/'):
                            download_url = f"http://jwxt.neu.edu.cn{file_url}"
                        else:
                            download_url = file_url
                        
                        print(f"[Avatar] 下载URL: {download_url}")
                        resp = self.get(download_url)
                        print(f"[Avatar] 下载状态: {resp.status_code}, Content-Type: {resp.headers.get('Content-Type')}")
                        
                        if resp.status_code == 200:
                            return resp.content
                else:
                    print("[Avatar] 没有文件items")
                    
            except ValueError as e:
                # 不是JSON，可能是直接图片数据
                print(f"[Avatar] JSON解析失败: {e}")
                if resp.status_code == 200:
                    return resp.content
                    
        except Exception as e:
            print(f"[Avatar] 获取头像失败: {e}")
            import traceback
            traceback.print_exc()
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
            # 只保存 pass.neu.edu.cn 域下的 cookie（CAS 登录状态）
            cas_cookies = {}
            for cookie in self._session.cookies:
                if "pass.neu.edu.cn" in cookie.domain or cookie.domain.endswith(".neu.edu.cn"):
                    cas_cookies[cookie.name] = {
                        "value": cookie.value,
                        "domain": cookie.domain,
                        "path": cookie.path,
                    }
            
            if cas_cookies:
                with open(self.cookie_file, 'wb') as f:
                    pickle.dump({
                        "username": self.username,
                        "cookies": cas_cookies,
                        "saved_at": time.time(),
                    }, f)
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
            
            with open(self.cookie_file, 'rb') as f:
                data = pickle.load(f)
            
            # 检查用户名是否匹配
            if data.get("username") != self.username:
                logger.debug("Cookie 用户名不匹配")
                return False
            
            # 恢复 cookies
            from requests.cookies import create_cookie
            for name, cookie_data in data.get("cookies", {}).items():
                cookie = create_cookie(
                    name=name,
                    value=cookie_data["value"],
                    domain=cookie_data["domain"],
                    path=cookie_data["path"],
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
            import os
            if os.path.exists(self.cookie_file):
                os.remove(self.cookie_file)
                logger.debug(f"Cookie 文件已删除: {self.cookie_file}")

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
                logger.info(f"票据刷新成功，当前 URL: {final_url}")
                self._logged_in = True
                self._save_cookies()  # 保存更新后的 cookies
                return True
            else:
                # 还在 CAS 页面，说明 Cookie 也失效了
                logger.debug("CAS Cookie 已失效，需要重新登录")
                return False
                
        except Exception as e:
            logger.warning(f"票据刷新失败: {e}")
            return False

    # ── 内部方法 ───────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_service_url(target: str) -> str:
        """解析 CAS service URL"""
        parsed = urlparse(target)
        host = parsed.netloc.lower()
        
        if "jwxt.neu.edu.cn" in host:
            return "http://jwxt.neu.edu.cn/jwapp/sys/homeapp/index.do"
        
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


# ── 异常 ──────────────────────────────────────────────────────────────────────

class NEULoginError(Exception):
    """登录失败异常"""
    pass
