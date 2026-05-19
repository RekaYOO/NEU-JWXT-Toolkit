from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class EvaluationSubmitRequest(BaseModel):
    """评教提交请求"""
    task_id: str = Field(..., description="评教任务ID（一级任务ID）")
    xspjid: str = Field(..., description="学生评教ID（课程评价记录ID）")
    strategy: str = Field(default="highest", description="评分策略: highest/lowest/custom")
    custom_scores: Optional[Dict[str, Any]] = Field(default=None, description="自定义分数映射")
    text_results: Optional[Dict[str, str]] = Field(default=None, description="文本型指标内容 {zbid: text}")


class EvaluationBatchRequest(BaseModel):
    """批量评教请求"""
    task_id: str = Field(..., description="评教任务ID")
    strategy: str = Field(default="highest", description="评分策略")
    custom_scores: Optional[Dict[str, Any]] = Field(default=None, description="自定义分数映射")
    delay: float = Field(default=2.0, description="提交间隔（秒）")
    xspjids: Optional[List[str]] = Field(default=None, description="选中的学生评教ID列表，为空则提交全部待评课程")
