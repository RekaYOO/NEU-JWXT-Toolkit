from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field


class CourseScoreModel(BaseModel):
    """完整的成绩模型 - 包含所有字段"""
    name: str
    code: str
    score: str = Field(..., description="成绩（数字或文字等级如'优'、'合格'）")
    score_value: float = Field(..., description="用于计算的数值（绩点+5)*10")
    gpa: float
    credit: float
    term: str
    term_display: str = Field(..., description="学期显示名，如'2024-2025学年春季学期'")
    course_type: str = Field(..., description="课程性质，如'必修/选修'")
    course_category: str = Field(..., description="课程类别，如'人文社会科学类'")
    general_category: str = Field(default="", description="通识选修类别，如'科学素养类'")
    exam_type: str = Field(..., description="考核方式，如'考试/考查'")
    exam_status: str = Field(default="", description="考试状态，如'初修/重修'")
    course_nature: str = Field(default="", description="课程性质代码")
    is_passed: bool


class TermScoresModel(BaseModel):
    term_code: str
    term_name: str
    courses: List[CourseScoreModel]
    total_credits: float
    gpa: float


class ScoresResponse(BaseModel):
    total_courses: int
    overall_gpa: Optional[float]
    calculated_gpa: float
    source: str = Field(..., description="数据来源: local/remote")
    is_fresh: bool = Field(..., description="是否最新数据")
    last_update: Optional[datetime] = None
    scores: List[CourseScoreModel]


class ColumnConfig(BaseModel):
    """列显示配置"""
    key: str
    title: str
    visible: bool = True
    width: Optional[int] = None
