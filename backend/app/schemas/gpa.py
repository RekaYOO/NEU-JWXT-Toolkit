from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class GPASimulationExportRequest(BaseModel):
    """GPA模拟导出请求"""
    filename: str = Field(..., description="文件名")
    data: Dict[str, Any] = Field(..., description="模拟数据")


class GPASimulationFile(BaseModel):
    """GPA模拟文件信息"""
    filename: str
    size: int
    modified_time: str
    stats: Optional[Dict] = None
