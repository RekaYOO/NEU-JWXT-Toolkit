"""
neu_storage - 本地数据存储模块

提供功能：
    - 成绩数据 CSV 存储
    - 登录配置本地保存
    - 数据自动加载/保存
    - 与 neu_auth/neu_academic 集成

使用示例：
    >>> from neu_auth import NEUAuthClient
    >>> from neu_storage import Storage, AcademicStorage, quick_save
    >>> 
    >>> auth = NEUAuthClient("学号", "密码")
    >>> auth.login()
    >>> 
    >>> # 方式1：手动存储
    >>> storage = Storage()
    >>> storage.save_scores(auth.academic.get_scores())
    >>> 
    >>> # 方式2：自动获取并保存
    >>> academic_storage = AcademicStorage()
    >>> result = academic_storage.fetch_and_save(auth)
    >>> 
    >>> # 方式3：一键保存所有
    >>> result = quick_save(auth)
"""

from .storage import Storage, StorageConfig
from .integration import AcademicStorage, AcademicReportStorage, AutoLoginManager, quick_save

__version__ = "1.0.0"
__all__ = [
    "Storage", 
    "StorageConfig",
    "AcademicStorage",
    "AcademicReportStorage",
    "AutoLoginManager", 
    "quick_save"
]
