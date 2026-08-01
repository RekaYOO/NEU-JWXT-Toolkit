from typing import List
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import PlainTextResponse

from backend.core.auth import NEUAuthClient
from backend.core.exam import ExamAPI
from backend.app.dependencies import require_serialized_auth
from backend.app.schemas import ExamListResponse, ExamItem, ExamTermsResponse, ExamTerm
from backend.core.log import log_application_error

router = APIRouter()


@router.get("/exams/terms", response_model=ExamTermsResponse)
def get_exam_terms(auth: NEUAuthClient = Depends(require_serialized_auth)):
    """获取考试学期列表"""
    try:
        api = ExamAPI(auth)
        terms = api.get_terms()
        current = None
        for t in terms:
            if t.get("selected") is True:
                current = t.get("itemCode")
                break
        if not current and terms:
            current = terms[0].get("itemCode")

        return ExamTermsResponse(
            terms=[
                ExamTerm(
                    item_code=t.get("itemCode", ""),
                    item_name=t.get("itemName", ""),
                    selected=t.get("selected") is True,
                )
                for t in terms
            ],
            current=current,
        )
    except Exception as e:
        error_id = log_application_error("exam.list_terms", e, 500)
        raise HTTPException(status_code=500, detail=f"获取学期列表失败（错误编号：{error_id}）") from e


@router.get("/exams", response_model=ExamListResponse)
def get_exams(
    term_code: str = Query("", description="学期代码，空字符串则使用当前学期"),
    auth: NEUAuthClient = Depends(require_serialized_auth)
):
    """获取考试安排列表"""
    try:
        api = ExamAPI(auth)

        # 获取学期名称
        terms = api.get_terms()
        term_name = term_code
        for t in terms:
            if t.get("itemCode") == term_code:
                term_name = t.get("itemName", term_code)
                break

        if not term_code:
            term_code = api.get_current_term()
            for t in terms:
                if t.get("itemCode") == term_code:
                    term_name = t.get("itemName", term_code)
                    break

        exams = api.get_exams(term_code)

        upcoming = [e for e in exams if e.exam_status == 0]
        ongoing = [e for e in exams if e.exam_status == 1]
        finished = [e for e in exams if e.exam_status == 2]

        return ExamListResponse(
            term_code=term_code or "",
            term_name=term_name,
            total=len(exams),
            upcoming=len(upcoming),
            ongoing=len(ongoing),
            finished=len(finished),
            exams=[
                ExamItem(
                    task_id=e.task_id,
                    course_name=e.course_name,
                    course_no=e.course_no,
                    course_desc=e.course_desc,
                    exam_type=e.exam_type,
                    exam_type_code=e.exam_type_code,
                    exam_status=e.exam_status,
                    exam_date=e.exam_date,
                    exam_time_description=e.exam_time_description,
                    week=e.week,
                    exam_place=e.exam_place,
                    exam_seat_no=e.exam_seat_no,
                    teachers=e.teachers,
                    teaching_class_id=e.teaching_class_id,
                    start_time=e.start_time,
                    end_time=e.end_time,
                )
                for e in exams
            ],
        )

    except Exception as e:
        error_id = log_application_error("exam.list", e, 500)
        raise HTTPException(status_code=500, detail=f"获取考试安排失败（错误编号：{error_id}）") from e


@router.get("/exams/export-ics")
def export_exams_ics(
    term_code: str = Query("", description="学期代码"),
    auth: NEUAuthClient = Depends(require_serialized_auth)
):
    """导出考试安排为 ICS 日历文件"""
    try:
        api = ExamAPI(auth)
        exams = api.get_exams(term_code)

        if not exams:
            raise HTTPException(status_code=404, detail="该学期暂无考试安排")

        ics_content = api.generate_ics(exams, student_name=auth.username)

        filename = f"exams_{term_code or 'current'}.ics"
        return PlainTextResponse(
            content=ics_content,
            media_type="text/calendar",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        error_id = log_application_error("exam.export_ics", e, 500)
        raise HTTPException(status_code=500, detail=f"导出 ICS 失败（错误编号：{error_id}）") from e
