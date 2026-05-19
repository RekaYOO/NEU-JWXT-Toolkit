from typing import Optional, List, Dict, Any
from pydantic import BaseModel


class CreditSummaryModel(BaseModel):
    """学分统计摘要"""
    total_required: float
    total_passed: float
    total_selected: float
    total_earned: float
    total_remaining: float
    completion_rate: float


class CourseModel(BaseModel):
    """课程模型"""
    course_name: str
    course_code: str
    course_nature: str
    credit: float
    score: Optional[str] = None
    is_passed: bool
    is_selected: bool
    is_planned: bool
    status: str
    status_display: str
    term_code: Optional[str] = None
    select_term_code: Optional[str] = None
    exam_type: Optional[str] = None
    is_core: bool = False
    substitute_course_name: Optional[str] = None
    substitute_credit: float = 0
    dept_code: Optional[str] = None
    category_path: List[str] = []


class CategoryNodeModel(BaseModel):
    """类别节点模型（支持递归嵌套）"""
    wid: str
    name: str
    category_code: str
    depth: int
    path: str
    path_array: List[str]
    is_leaf: bool
    required_credits: float
    passed_credits: float
    selected_credits: float
    planned_credits: float
    earned_credits: float
    remaining_credits: float
    completion_rate: float
    is_completed: bool
    courses: List[CourseModel]
    children: List[Any]  # 递归类型，使用Any


class AcademicReportResponse(BaseModel):
    """培养计划响应"""
    student_name: str
    student_id: str
    grade: str = ""
    college: str = ""
    major: str = ""
    class_name: str = ""
    expected_graduation: str = ""
    program_code: str
    program_name: str = ""
    calculated_time: str
    credit_summary: CreditSummaryModel
    categories: List[CategoryNodeModel]
    outside_courses: List[CourseModel]
    source: str = "remote"
    is_fresh: bool = True
    last_update: Optional[str] = None  # 本地保存时间 ISO 格式
