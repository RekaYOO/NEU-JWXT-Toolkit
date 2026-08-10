from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from backend.core.course_outline import normalize_course_code


class CourseOutlineSearchRequest(BaseModel):
    keyword: str = Field("", max_length=80)
    filters: dict[str, Any] = Field(default_factory=dict)
    page: int = Field(1, ge=1, le=10000)
    page_size: int = Field(20, ge=1, le=100)

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, value: dict[str, Any]) -> dict[str, Any]:
        allowed = {"KCH", "KCM", "KKDWDM", "KCCCDM", "KCJBDM", "XF", "XS"}
        if set(value) - allowed:
            raise ValueError("unsupported course-outline filter")
        return value


class CourseOutlineDetailRequest(BaseModel):
    course_code: str

    @field_validator("course_code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return normalize_course_code(value)


class CourseOutlineSectionsRequest(CourseOutlineDetailRequest):
    group: Literal["teaching", "assessment", "governance"]


class CourseOutlineAttachmentRequest(BaseModel):
    token: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._-]+$")
    filename: str = Field("课程大纲附件", min_length=1, max_length=120)


class CourseOutlineMetadataSyncRequest(BaseModel):
    courses: list[dict[str, Any]] = Field(default_factory=list, max_length=800)
    force: bool = False
