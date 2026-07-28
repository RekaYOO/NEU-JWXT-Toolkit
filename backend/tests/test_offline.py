import asyncio

from backend.app.routers import offline
from backend.core.academic.api import CourseScore
from backend.core.storage import AcademicReportStorage, Storage
from backend.core.storage.storage import StorageConfig


def _score():
    return CourseScore(
        name="离线测试课程",
        code="OFFLINE-1",
        score="90",
        gpa=4.0,
        credit=2.0,
        term="2025-2026-2",
        term_display="2025-2026学年春季学期",
        course_type="必修",
        course_category="专业课",
        exam_type="考试",
        is_passed=True,
    )


def test_offline_routes_read_only_local_caches(monkeypatch, tmp_path):
    storage = Storage(StorageConfig(data_dir=str(tmp_path)))
    report_storage = AcademicReportStorage(storage)
    storage.save_scores(
        [_score()],
        metadata={"username": "20250001", "overall_gpa": 4.0},
    )
    report_storage.save_report(
        {
            "student_name": "离线用户",
            "student_id": "20250001",
            "program_code": "P1",
            "calculated_time": "2026-07-28",
            "credit_summary": {
                "total_required": 160,
                "total_passed": 2,
                "total_selected": 0,
                "total_earned": 2,
                "total_remaining": 158,
                "completion_rate": 1.25,
            },
            "categories": [],
            "outside_courses": [],
        },
        "20250001",
    )
    monkeypatch.setattr(offline, "_storage", storage)
    monkeypatch.setattr(offline, "_report_storage", report_storage)

    status = asyncio.run(offline.offline_status())
    scores = asyncio.run(offline.offline_scores())
    report = asyncio.run(offline.offline_academic_report())

    assert status == {
        "available": True,
        "has_scores": True,
        "has_report": True,
        "username": "20250001",
        "read_only": True,
    }
    assert scores.source == "offline"
    assert scores.calculated_gpa == 4.0
    assert report.source == "offline"
    assert report.student_id == "20250001"
