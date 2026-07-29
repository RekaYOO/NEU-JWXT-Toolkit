"""
neu_storage - 本地数据存储模块

提供功能：
    - 成绩数据 CSV 存储
    - 登录配置本地保存
    - 旧版 CSV/JSON 的只读迁移与显式导出
    - 登录配置本地保存

运行时学业缓存统一由 ``backend.core.cache`` 管理。AcademicStorage 和
AcademicReportStorage 仅保留一个兼容周期，禁止新业务调用其 smart/refresh
方法建立第二套缓存。
"""

from .storage import Storage, StorageConfig
from .integration import AcademicStorage, AcademicReportStorage, AutoLoginManager, quick_save
from .research import ResearchTrainingStorage

__version__ = "1.0.0"
__all__ = [
    "Storage", 
    "StorageConfig",
    "AcademicStorage",
    "AcademicReportStorage",
    "AutoLoginManager", 
    "ResearchTrainingStorage",
    "quick_save"
]
