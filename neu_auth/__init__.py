# neu_auth - 东北大学统一身份认证登录封装包
# 支持自动登录 pass.neu.edu.cn，维持 CAS 会话，供后续接口调用使用

from .client import NEUAuthClient

__all__ = ["NEUAuthClient"]
__version__ = "1.0.0"
