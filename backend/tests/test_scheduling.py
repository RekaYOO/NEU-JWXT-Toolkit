from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.app.routers import scheduling as scheduling_router
from backend.app.schemas.scheduling import (
    ScheduleConflictBatchRequest,
    ScheduleMeetingInput,
)
from backend.core.scheduling import (
    ConflictStatus,
    check_conflicts,
    normalize_meeting,
    parse_weeks,
)


def meeting(**changes):
    payload = {
        "course_name": "软件工程",
        "course_code": "C-1",
        "teaching_class_id": "TC-1",
        "weeks": list(range(1, 9)),
        "weekday": 3,
        "start_section": 3,
        "end_section": 4,
    }
    payload.update(changes)
    return normalize_meeting(payload, term_code="2025-2026-2")


def test_parse_weeks_normalizes_ranges_lists_parity_and_exclusions():
    assert parse_weeks("第1-8周（单周），除5周") == (1, 3, 7)
    assert parse_weeks("2～8周(双)") == (2, 4, 6, 8)
    assert parse_weeks("1, 3、6周") == (1, 3, 6)
    assert parse_weeks([{"weeks": "1-2周"}, 4, 99, "bad"]) == (1, 2, 4)
    assert parse_weeks("1-4周/教师12/5-6周/教师2") == (1, 2, 3, 4, 5, 6)
    assert parse_weeks("8-3周") == ()
    assert parse_weeks(
        "2-4周 卢震 浑南校区 信息化管理实验室(文管学馆B208) 第1实验班",
        strict_mixed_text=True,
    ) == (2, 3, 4)
    assert parse_weeks(
        "第2-16周（双周） 1号楼A101 第1-2节",
        strict_mixed_text=True,
    ) == (2, 4, 6, 8, 10, 12, 14, 16)
    assert parse_weeks(
        [{"weeks": "9周,11-17周", "name": "第1实验班"}],
        strict_mixed_text=True,
    ) == (9, 11, 12, 13, 14, 15, 16, 17)
    assert parse_weeks(
        "1-4周(单)/教师甲/5-8周(双)/教师乙",
        strict_mixed_text=True,
    ) == (1, 3, 6, 8)


def test_normalization_generates_stable_occurrence_id_and_keeps_legacy_shape_distinct():
    first = meeting()
    again = meeting()
    later = meeting(start_section=5, end_section=6)

    assert first.meeting_id == again.meeting_id
    assert first.meeting_id.startswith("mtg_")
    assert first.meeting_id != later.meeting_id
    assert first.source_id == "TC-1"
    assert first.weeks == tuple(range(1, 9))


def test_normalization_accepts_experiment_round_display_fields():
    normalized = normalize_meeting(
        {
            "candidate_id": "round-1",
            "course_name": "物理实验",
            "week": "2-8周（双周）",
            "day": "星期三",
            "time": "第5-6节",
            "course_type": "实验",
        },
        term_code="2025-2026-2",
        default_source="candidate",
    )

    assert normalized.source_id == "round-1"
    assert normalized.weeks == (2, 4, 6, 8)
    assert normalized.weekday == 3
    assert (normalized.start_section, normalized.end_section) == (5, 6)
    assert normalized.activity_type == "experiment"


def test_conflict_detection_reports_overlap_clear_and_unknown_without_guessing():
    baseline = [meeting()]
    conflict = meeting(
        candidate_id="round-conflict",
        teaching_class_id="",
        weeks=[5, 6, 9],
        course_name="实验课",
    )
    clear = meeting(
        candidate_id="round-clear",
        teaching_class_id="",
        weeks=[10],
        course_name="另一实验课",
    )
    unknown = meeting(
        candidate_id="round-unknown",
        teaching_class_id="",
        weeks=[],
        course_name="周次未知实验",
    )

    results = check_conflicts(baseline, [conflict, clear, unknown])

    assert [result.status for result in results] == [
        ConflictStatus.CONFLICT,
        ConflictStatus.CLEAR,
        ConflictStatus.UNKNOWN,
    ]
    assert results[0].matches[0].overlapping_weeks == (5, 6)
    assert results[0].matches[0].reason == "section_overlap"
    assert results[0].matches[0].baseline_course_code == "C-1"
    assert results[0].matches[0].baseline_weeks == tuple(range(1, 9))
    assert results[0].matches[0].weekday == 3
    assert (results[0].matches[0].start_section, results[0].matches[0].end_section) == (3, 4)
    assert results[2].matches[0].reason == "unknown_weeks"


def test_incomplete_candidate_is_unknown_even_when_baseline_is_empty():
    candidate = meeting(
        candidate_id="unknown-empty-baseline",
        teaching_class_id="",
        weeks=[],
        weekday=0,
        start_section=0,
        end_section=0,
    )

    result = check_conflicts([], [candidate])[0]

    assert result.status is ConflictStatus.UNKNOWN


def test_conflict_detection_uses_clock_time_when_sections_are_unavailable():
    baseline = [meeting(start_section=0, end_section=0, start_time="08:30", end_time="10:00")]
    adjacent = meeting(
        candidate_id="adjacent",
        teaching_class_id="",
        start_section=0,
        end_section=0,
        start_time="10:00",
        end_time="11:30",
    )
    overlapping = meeting(
        candidate_id="overlap",
        teaching_class_id="",
        start_section=0,
        end_section=0,
        start_time="09:30",
        end_time="10:30",
    )

    results = check_conflicts(baseline, [adjacent, overlapping])
    assert [result.status for result in results] == [
        ConflictStatus.CLEAR,
        ConflictStatus.CONFLICT,
    ]
    assert results[1].matches[0].reason == "time_overlap"


def test_preview_conflict_detection_ignores_the_same_course_only_when_requested():
    baseline = meeting(
        course_name="软件 工程",
        course_code="C-1",
        weeks=[2],
        weekday=1,
        start_section=1,
        end_section=2,
    )
    same_course = meeting(
        candidate_id="same",
        course_name="软件工程",
        course_code="C-1",
        weeks=[2],
        weekday=1,
        start_section=2,
        end_section=3,
    )
    other_course = meeting(
        candidate_id="other",
        course_name="操作系统",
        course_code="C-2",
        weeks=[2],
        weekday=1,
        start_section=2,
        end_section=3,
    )

    ordinary = check_conflicts([baseline], [same_course])[0]
    preview = check_conflicts(
        [baseline], [same_course, other_course], ignore_same_course=True
    )

    assert ordinary.status is ConflictStatus.CONFLICT
    assert preview[0].status is ConflictStatus.CLEAR
    assert preview[1].status is ConflictStatus.CONFLICT


def test_batch_conflict_route_uses_current_account_term_cache(monkeypatch):
    entry = SimpleNamespace(
        revision="v1:cached",
        payload={
            "term_code": "2025-2026-2",
            "courses": [{
                "id": "legacy-class",
                "course_name": "软件工程",
                "weeks": [1, 2, 3],
                "weekday": 1,
                "start_section": 1,
                "end_section": 2,
            }],
        },
    )

    class Coordinator:
        def read(self, **kwargs):
            assert kwargs == {
                "account_id": "account-a",
                "resource": "personal-timetable",
                "variant": "term:2025-2026-2",
            }
            return entry, True

    monkeypatch.setattr(scheduling_router, "get_cache_coordinator", lambda: Coordinator())
    request = ScheduleConflictBatchRequest(
        term_code="2025-2026-2",
        candidates=[ScheduleMeetingInput(
            candidate_id="experiment-1",
            course_name="物理实验",
            weeks=[2],
            weekday=1,
            start_section=2,
            end_section=3,
        )],
    )

    response = scheduling_router.check_schedule_conflicts(
        request, auth=SimpleNamespace(username="account-a")
    )

    assert response.baseline_available is True
    assert response.baseline_revision == "v1:cached"
    assert response.baseline_stale is True
    assert response.results[0].status == "unknown"
    assert response.results[0].matches[0].baseline_course_name == "软件工程"


def test_batch_conflict_route_returns_unknown_when_cache_is_missing(monkeypatch):
    coordinator = SimpleNamespace(read=lambda **_kwargs: (None, True))
    monkeypatch.setattr(scheduling_router, "get_cache_coordinator", lambda: coordinator)
    request = ScheduleConflictBatchRequest(
        term_code="2025-2026-2",
        week=6,
        candidates=[ScheduleMeetingInput(
            candidate_id="experiment-1",
            course_name="物理实验",
            weekday=1,
            start_section=1,
            end_section=2,
        )],
    )

    response = scheduling_router.check_schedule_conflicts(
        request, auth=SimpleNamespace(username="account-b")
    )

    assert response.baseline_available is False
    assert response.baseline_revision is None
    assert response.baseline_stale is True
    assert response.results[0].status == "unknown"


def test_preview_conflict_check_always_prefers_live_personal_timetable_over_fresh_cache(monkeypatch):
    cached_entry = SimpleNamespace(
        revision="v1:old",
        payload={
            "term_code": "2025-2026-2",
            "courses": [{
                "course_name": "缓存中已不存在的课程",
                "course_code": "OLD-1",
                "weeks": [2],
                "weekday": 1,
                "start_section": 1,
                "end_section": 2,
            }],
        },
    )
    coordinator = SimpleNamespace(read=lambda **_kwargs: (cached_entry, False))
    monkeypatch.setattr(scheduling_router, "get_cache_coordinator", lambda: coordinator)
    monkeypatch.setattr(
        scheduling_router,
        "remote_session_guard",
        lambda: __import__("contextlib").nullcontext(),
    )
    timetable = SimpleNamespace(get_schedule=lambda **kwargs: {
        "courses": [{
            "course_name": "实时个人课程",
            "course_code": "LIVE-1",
            "weeks": [2],
            "weekday": 1,
            "start_section": 3,
            "end_section": 4,
        }],
    })
    request = ScheduleConflictBatchRequest(
        term_code="2025-2026-2",
        resolve_personal_timetable=True,
        candidates=[ScheduleMeetingInput(
            candidate_id="candidate-1",
            course_name="候选课程",
            weeks=[2],
            weekday=1,
            start_section=1,
            end_section=2,
        )],
    )

    response = scheduling_router.check_schedule_conflicts(
        request,
        auth=SimpleNamespace(username="account-a", timetable=timetable),
    )

    assert response.baseline_available is True
    assert response.baseline_revision is None
    assert response.baseline_stale is False
    assert response.results[0].status == "clear"
    assert response.results[0].matches == []


def test_batch_conflict_route_does_not_treat_malformed_cache_as_an_empty_schedule(monkeypatch):
    entry = SimpleNamespace(
        revision="v1:bad",
        payload={"term_code": "2025-2026-2", "courses": "not-a-list"},
    )
    coordinator = SimpleNamespace(read=lambda **_kwargs: (entry, False))
    monkeypatch.setattr(scheduling_router, "get_cache_coordinator", lambda: coordinator)
    request = ScheduleConflictBatchRequest(
        term_code="2025-2026-2",
        candidates=[ScheduleMeetingInput(
            candidate_id="experiment-1", course_name="物理实验"
        )],
    )

    response = scheduling_router.check_schedule_conflicts(
        request, auth=SimpleNamespace(username="account-b")
    )

    assert response.baseline_available is False
    assert response.baseline_stale is True
    assert response.results[0].status == "unknown"


def test_scheduling_contract_bounds_batches_and_rejects_partial_time_ranges():
    with pytest.raises(ValidationError):
        ScheduleConflictBatchRequest(
            term_code="2025-2026-2",
            candidates=[
                ScheduleMeetingInput(course_name=f"课程{i}")
                for i in range(501)
            ],
        )
    with pytest.raises(ValidationError):
        ScheduleMeetingInput(
            course_name="错误课程",
            start_time="08:30",
        )
    with pytest.raises(ValidationError):
        ScheduleMeetingInput(
            course_name="错误课程",
            start_section=4,
            end_section=2,
        )
    with pytest.raises(ValidationError):
        ScheduleMeetingInput(course_name="错误课程", weeks=[0, 31])
    with pytest.raises(ValidationError):
        ScheduleMeetingInput(
            course_name="错误课程", start_time="25:00", end_time="26:00"
        )
