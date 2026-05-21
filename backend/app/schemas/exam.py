from typing import List, Optional
from pydantic import BaseModel


class ExamTerm(BaseModel):
    item_code: str
    item_name: str
    selected: Optional[bool] = None


class ExamItem(BaseModel):
    task_id: str
    course_name: str
    course_no: str
    course_desc: str
    exam_type: str
    exam_type_code: str
    exam_status: int
    exam_date: str
    exam_time_description: str
    week: int
    exam_place: str
    exam_seat_no: str
    teachers: str
    teaching_class_id: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None


class ExamListResponse(BaseModel):
    term_code: str
    term_name: str
    total: int
    upcoming: int
    ongoing: int
    finished: int
    exams: List[ExamItem]


class ExamTermsResponse(BaseModel):
    terms: List[ExamTerm]
    current: Optional[str] = None
