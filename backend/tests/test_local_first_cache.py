import inspect
from datetime import datetime
from types import SimpleNamespace

from backend.app.routers import report as report_router
from backend.app.routers import research as research_router
from backend.app.routers import scores as scores_router
from backend.core.academic.api import CourseScore
from backend.core.storage import AcademicReportStorage, AcademicStorage, Storage
from backend.core.storage.storage import StorageConfig


def _score() -> CourseScore:
    return CourseScore(
        name="缓存测试课程",
        code="CACHE-1",
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


def test_legacy_score_cache_is_account_bound_for_migration(tmp_path):
    storage = Storage(StorageConfig(data_dir=str(tmp_path)))
    academic = AcademicStorage(storage)
    storage.save_scores(
        [_score()],
        metadata={"username": "20250001", "overall_gpa": 4.0},
    )

    cached = academic.get_cached_scores("20250001")
    assert cached is not None
    assert cached["source"] == "local"
    assert academic.get_cached_scores("20250002") is None


def test_legacy_report_cache_is_account_bound_for_migration(tmp_path):
    storage = Storage(StorageConfig(data_dir=str(tmp_path)))
    report_storage = AcademicReportStorage(storage)
    report_storage.save_report({"categories": []}, "20250001")

    cached = report_storage.get_cached_report("20250001")
    assert cached is not None
    assert report_storage.get_cached_report("20250002") is None


def test_cache_routes_do_not_need_a_remote_auth_check(monkeypatch, tmp_path):
    storage = Storage(StorageConfig(data_dir=str(tmp_path)))
    report_storage = AcademicReportStorage(storage)
    storage.save_scores(
        [_score()],
        metadata={"username": "20250001", "overall_gpa": 4.0},
    )
    report_storage.save_report(
        {
            "program_code": "P1",
            "calculated_time": "2026-07-29",
            "credit_summary": {
                "total_required": 1,
                "total_passed": 1,
                "total_selected": 0,
                "total_earned": 1,
                "total_remaining": 0,
                "completion_rate": 100,
            },
            "categories": [],
            "outside_courses": [],
        },
        "20250001",
    )
    class Entry:
        saved_at = datetime.now()
        payload = {
            "scores": [{
                "name": "缓存测试课程", "code": "CACHE-1", "score": "90",
                "gpa": 4.0, "credit": 2.0, "term": "2025-2026-2",
                "term_display": "2025-2026学年春季学期",
                "course_type": "必修", "course_category": "专业课",
                "general_category": "", "exam_type": "考试",
                "exam_status": "", "course_nature": "", "is_passed": True,
            }],
            "overall_gpa": 4.0,
        }
        def metadata(self, *, is_stale):
            return {"revision": "v1:test", "is_stale": is_stale}
    score_entry = Entry()
    report_entry = Entry()
    report_entry.payload = report_storage.load_report()["report"]
    monkeypatch.setattr(
        scores_router, "read_cache",
        lambda account, resource: (score_entry, False),
    )
    monkeypatch.setattr(
        report_router, "read_cache",
        lambda account, resource: (report_entry, False),
    )
    identity = SimpleNamespace(username="20250001", is_logged_in=True)

    assert scores_router.get_cached_scores(identity).source == "local"
    assert report_router.get_cached_academic_report(identity).source == "local"


def test_blocking_research_handlers_run_in_fastapi_threadpool():
    handlers = [
        research_router.refresh_research_training,
        research_router.get_research_training,
        research_router.get_research_topic,
        research_router.get_confirmed_research_topics,
        research_router.enroll_research_topic,
        research_router.cancel_research_enrollment,
    ]
    assert all(not inspect.iscoroutinefunction(handler) for handler in handlers)
