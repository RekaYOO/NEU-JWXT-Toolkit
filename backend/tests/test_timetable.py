import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.app.schemas.timetable import (
    TimetableContextRequest,
    TimetableScheduleRequest,
    TimetableTargetSearchRequest,
)
from backend.core.auth.client import NEULoginError
from backend.core.timetable import TimetableAPI, TimetableError
from backend.app.routers import timetable as timetable_router


class Response:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class Client:
    def __init__(self, *bodies):
        self.bodies = list(bodies)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.bodies.pop(0))


def test_terms_use_official_default_and_available_term_models():
    client = Client(
        {
            "code": "0",
            "datas": {"cxmrxnxq": {"rows": [{"XNXQDM": "2025-2026-2"}]}},
        },
        {
            "code": "0",
            "datas": {
                "xnxqcx": {
                    "rows": [
                        {"DM": "2025-2026-2", "MC": "春季学期"},
                        {"DM": "2025-2026-1", "MC": "秋季学期"},
                    ]
                }
            },
        },
    )

    terms = TimetableAPI(client).get_terms()

    assert [item["code"] for item in terms] == ["2025-2026-2", "2025-2026-1"]
    assert terms[0]["current"] is True
    assert all(call[0].startswith("https://jwxt.neu.edu.cn/") for call in client.calls)
    assert all(call[1]["headers"]["X-Requested-With"] == "XMLHttpRequest" for call in client.calls)


@pytest.mark.parametrize("current_payload", [
    {"code": "0", "datas": {"cxmrxnxq": "2025-2026-2"}},
    {"code": "0", "datas": {"cxmrxnxq": {"value": "2025-2026-2"}}},
    {"code": "0", "datas": {"currentTerm": "2025-2026-2"}},
])
def test_terms_accept_official_current_term_scalar_and_object_shapes(current_payload):
    client = Client(
        current_payload,
        {"code": "0", "datas": {"xnxqcx": {"rows": [
            {"DM": "2025-2026-2", "MC": "春季学期"},
            {"DM": "2026-2027-1", "MC": "秋季学期"},
        ]}}},
    )

    terms = TimetableAPI(client).get_terms()

    assert terms[0]["current"] is True
    assert terms[1]["current"] is False


def test_terms_reuse_short_lived_in_process_cache_and_return_copies():
    client = Client(
        {"code": "0", "datas": {"cxmrxnxq": "2025-2026-2"}},
        {"code": "0", "datas": {"xnxqcx": {"rows": [
            {"DM": "2025-2026-2", "MC": "春季学期"},
        ]}}},
    )
    api = TimetableAPI(client)

    first = api.get_terms()
    first[0]["name"] = "被调用方修改"
    second = api.get_terms()
    cached = api.get_cached_terms()

    assert len(client.calls) == 2
    assert second[0]["name"] == "春季学期"
    assert cached == second
    assert cached is not second


def test_terms_route_uses_cached_catalog_without_remote_session(monkeypatch):
    terms = [{"code": "2025-2026-2", "name": "春季学期", "current": True}]

    class Timetable:
        def get_cached_terms(self):
            return [dict(item) for item in terms]

        def get_terms(self):
            raise AssertionError("cached route must not access the remote timetable service")

    auth = type("Auth", (), {"username": "student", "timetable": Timetable()})()

    def forbidden_guard():
        raise AssertionError("cached route must not acquire the remote session guard")

    monkeypatch.setattr(timetable_router, "remote_session_guard", forbidden_guard)
    response = timetable_router.get_timetable_terms(auth=auth)

    assert response.current == "2025-2026-2"


def test_terms_route_fences_identity_before_remote_refresh(monkeypatch):
    class Timetable:
        def get_cached_terms(self):
            return None

        def get_terms(self):
            raise AssertionError("stale identity must not access the remote timetable service")

    auth = type("Auth", (), {"username": "student", "timetable": Timetable()})()
    monkeypatch.setattr(timetable_router, "get_auth_generation", lambda: 7)
    monkeypatch.setattr(
        timetable_router,
        "auth_generation_is_current",
        lambda generation, account: False,
    )

    with pytest.raises(HTTPException) as error:
        timetable_router.get_timetable_terms(auth=auth)

    assert error.value.status_code == 401


def test_week_current_flag_does_not_treat_false_text_as_true():
    client = Client({
        "code": "0",
        "datas": [
            {"serialNumber": 1, "name": "第1周", "curWeek": "false"},
            {"serialNumber": 2, "name": "第2周", "curWeek": "true"},
        ],
    })

    weeks = TimetableAPI(client).get_weeks("2025-2026-2")

    assert [week["current"] for week in weeks] == [False, True]


def test_week_ranges_start_on_sunday_without_shifting_existing_sunday_ranges():
    client = Client({
        "code": "0",
        "datas": [
            {"serialNumber": 1, "startDate": "2026-08-31", "endDate": "2026-09-06"},
            {"serialNumber": 2, "startDate": "2026-09-06", "endDate": "2026-09-12"},
        ],
    })

    weeks = TimetableAPI(client).get_weeks("2026-2027-1")

    assert (weeks[0]["start_date"], weeks[0]["end_date"]) == ("2026-08-30", "2026-09-05")
    assert (weeks[1]["start_date"], weeks[1]["end_date"]) == ("2026-09-06", "2026-09-12")


def test_sections_send_selected_campus_and_use_official_section_number():
    client = Client({
        "code": "0",
        "datas": [{
            "sectionNumber": 8,
            "name": "第8节",
            "startTime": "15:55",
            "endTime": "16:40",
        }],
    })

    sections = TimetableAPI(client).get_sections(
        "2025-2026-2", mode="personal", campus_code="02"
    )

    assert sections[0]["number"] == 8
    assert client.calls[0][1]["data"]["XQDM"] == "02"


def test_multiple_campuses_include_an_all_campuses_query_without_losing_courses():
    campus_client = Client({
        "code": "0",
        "datas": [
            {"id": "01", "name": "南湖校区"},
            {"id": "02", "name": "浑南校区"},
        ],
    })
    campuses = TimetableAPI(campus_client).get_campuses("2025-2026-2")
    assert [campus["code"] for campus in campuses] == ["all", "01", "02"]

    schedule_client = Client({
        "code": "0",
        "datas": {"arrangedList": [], "notArrangeList": [], "practiceList": []},
    })
    TimetableAPI(schedule_client).get_schedule(
        mode="personal", term_code="2025-2026-2", campus_code="all"
    )
    assert schedule_client.calls[0][1]["data"]["XQDM"] == ""


def test_personal_single_reported_campus_still_offers_all_campuses_view():
    client = Client({"code": "0", "datas": [{"id": "01", "name": "南湖校区"}]})

    campuses = TimetableAPI(client).get_campuses("2025-2026-2", mode="personal")

    assert [campus["code"] for campus in campuses] == ["all", "01"]


def test_future_personal_term_without_campus_catalog_still_offers_all_view():
    client = Client({"code": "0", "datas": []})

    campuses = TimetableAPI(client).get_campuses("2026-2027-1", mode="personal")

    assert campuses == [{"code": "all", "name": "全部校区"}]


def test_target_search_uses_bounded_emap_query_and_maps_public_metadata():
    client = Client(
        {
            "code": "0",
            "datas": {
                "room_list": {
                    "rows": [{
                        "CODE": "ROOM-1",
                        "JASMC": "示例教室",
                        "XXXQDM": "CAMPUS-1",
                        "XXXQDM_DISPLAY": "示例校区",
                        "JXLDM": "BUILDING-1",
                        "JXLDM_DISPLAY": "示例楼",
                        "RL": 80,
                        "SFPK_DISPLAY": "是",
                    }],
                    "totalSize": 1,
                    "pageNumber": 1,
                    "pageSize": 20,
                }
            },
        }
    )

    result = TimetableAPI(client).search_targets(
        "room", "2025-2026-2", keyword="示例", page=1, page_size=20
    )

    assert result["total"] == 1
    assert result["items"][0] == {
        "id": "ROOM-1",
        "name": "示例教室",
        "has_schedule": "是",
        "details": {"campus": "示例校区", "building": "示例楼", "capacity": "80"},
        "filter_values": {"campus": "CAMPUS-1", "building": "BUILDING-1"},
    }
    url, options = client.calls[0]
    assert url.endswith("/modules/qxkbcx/jslb.do")
    assert options["data"]["pageSize"] == 20
    rules = json.loads(options["data"]["querySetting"])
    assert rules == [{
        "name": "JASMC",
        "builder": "include",
        "linkOpt": "AND",
        "value": "示例",
    }]


def test_target_search_maps_mode_filters_to_fixed_official_fields():
    client = Client({
        "code": "0",
        "datas": {"bjlb": {"rows": [], "totalSize": 0, "pageNumber": 1, "pageSize": 20}},
    })

    TimetableAPI(client).search_targets(
        "class",
        "2025-2026-2",
        filters={"grade": "25", "college": "08", "has_schedule": "yes"},
    )

    rules = json.loads(client.calls[0][1]["data"]["querySetting"])
    assert rules == [
        {"name": "BJMC", "builder": "include", "linkOpt": "AND", "value": "25"},
        {"name": "YXDM", "builder": "equal", "linkOpt": "AND", "value": "08"},
        {"name": "SFPK", "builder": "equal", "linkOpt": "AND", "value": "1"},
    ]


def test_target_search_rejects_filters_from_another_mode():
    with pytest.raises(ValidationError):
        TimetableTargetSearchRequest(
            mode="teacher",
            term_code="2025-2026-2",
            filters={"building": "信息楼"},
        )


def test_room_target_supports_floor_and_complete_capacity_range():
    client = Client({
        "code": "0",
        "datas": {"jslb": {"rows": [], "totalSize": 0, "pageNumber": 1, "pageSize": 20}},
    })

    TimetableAPI(client).search_targets(
        "room",
        "2025-2026-2",
        filters={"floor": "3.0", "min_capacity": 60, "max_capacity": 120},
    )

    rules = json.loads(client.calls[0][1]["data"]["querySetting"])
    assert rules == [
        {"name": "LC", "builder": "equal", "linkOpt": "AND", "value": "3.0"},
        {"name": "RL", "builder": "moreEqual", "linkOpt": "AND", "value": "60"},
        {"name": "RL", "builder": "lessEqual", "linkOpt": "AND", "value": "120"},
    ]


def test_room_target_rejects_inverted_capacity_range():
    with pytest.raises(ValidationError):
        TimetableTargetSearchRequest(
            mode="room",
            term_code="2025-2026-2",
            filters={"min_capacity": 120, "max_capacity": 60},
        )


def test_class_target_derives_grade_filter_from_the_class_name_when_remote_omits_it():
    target = TimetableAPI._target_from_row(
        "class",
        {"CODE": "14032402", "BJMC": "工业2402", "YXDM": "14", "YXDM_DISPLAY": "工商管理学院"},
    )

    assert target["details"]["grade"] == "2024级"
    assert target["filter_values"]["grade"] == "24"


def test_target_filter_catalog_scans_all_pages_and_reuses_short_lived_cache():
    client = Client(
        {"code": "0", "datas": {"bjlb": {
            "rows": [{
                "CODE": "CLASS-1", "BJMC": "示例2401",
                "YXDM": "14", "YXDM_DISPLAY": "A学院",
                "ZYDM": "1401", "ZYDM_DISPLAY": "A专业",
            }],
            "totalSize": 1001, "pageNumber": 1, "pageSize": 1000,
        }}},
        {"code": "0", "datas": {"bjlb": {
            "rows": [{
                "CODE": "CLASS-2", "BJMC": "示例2501",
                "YXDM": "08", "YXDM_DISPLAY": "B学院",
                "ZYDM": "0801", "ZYDM_DISPLAY": "B专业",
            }],
            "totalSize": 1001, "pageNumber": 2, "pageSize": 1000,
        }}},
    )
    api = TimetableAPI(client)

    first = api.get_target_filter_options("class", "2025-2026-2")
    second = api.get_target_filter_options("class", "2025-2026-2")

    assert first == second
    assert first["options"]["college"] == [
        {"value": "14", "label": "A学院"},
        {"value": "08", "label": "B学院"},
    ]
    assert first["options"]["major"] == [
        {"value": "1401", "label": "A专业"},
        {"value": "0801", "label": "B专业"},
    ]
    assert len(client.calls) == 2
    assert all(call[1]["data"]["pageSize"] == 1000 for call in client.calls)
    assert {tuple(sorted(item.items())) for item in first["relations"]} == {
        (("college", "14"), ("grade", "24"), ("major", "1401")),
        (("college", "08"), ("grade", "25"), ("major", "0801")),
    }


def test_room_filter_catalog_uses_the_public_type_label():
    client = Client({"code": "0", "datas": {"jslb": {
        "rows": [{
            "CODE": "ROOM-1", "JASMC": "示例教室",
            "JASLXDM": "01", "JASLXDM_DISPLAY": "多媒体教室",
        }],
        "totalSize": 1, "pageNumber": 1, "pageSize": 1000,
    }}})

    options = TimetableAPI(client).get_target_filter_options("room", "2025-2026-2")

    assert options["options"]["room_type"] == [{"value": "01", "label": "多媒体教室"}]


def test_room_filter_catalog_includes_floor_values():
    client = Client({"code": "0", "datas": {"jslb": {
        "rows": [{
            "CODE": "ROOM-1", "JASMC": "示例教室", "LC": "3.0",
        }],
        "totalSize": 1, "pageNumber": 1, "pageSize": 1000,
    }}})

    options = TimetableAPI(client).get_target_filter_options("room", "2025-2026-2")

    assert options["options"]["floor"] == [{"value": "3.0", "label": "3.0"}]


def test_target_filter_catalog_honors_the_page_size_returned_by_emap():
    client = Client(*[
        {"code": "0", "datas": {"lslb": {
            "rows": [{
                "CODE": f"T-{page}", "JSMC": f"教师{page}",
                "SZDWDM": f"D-{page}", "SZDWDM_DISPLAY": f"单位{page}",
            }],
            "totalSize": 101, "pageNumber": page, "pageSize": 50,
        }}}
        for page in (1, 2, 3)
    ])

    options = TimetableAPI(client).get_target_filter_options("teacher", "2025-2026-2")

    assert len(client.calls) == 3
    assert len(options["options"]["department"]) == 3


def test_target_filter_catalog_cache_is_bounded_lru():
    client = Client(*[
        {"code": "0", "datas": {"bjlb": {
            "rows": [], "totalSize": 0, "pageNumber": 1, "pageSize": 1000,
        }}}
        for _ in range(3)
    ])
    api = TimetableAPI(client)
    api.FILTER_CATALOG_MAX_CACHE_ENTRIES = 2

    for term_code in ("TERM-1", "TERM-2", "TERM-3"):
        api.get_target_filter_options("class", term_code)

    assert list(api._filter_catalog_cache) == [("class", "TERM-2"), ("class", "TERM-3")]


def test_target_filter_catalog_rejects_a_repeated_official_page():
    client = Client(
        {"code": "0", "datas": {"bjlb": {
            "rows": [{"CODE": "C-1", "BJMC": "班级1"}],
            "totalSize": 101, "pageNumber": 1, "pageSize": 50,
        }}},
        {"code": "0", "datas": {"bjlb": {
            "rows": [{"CODE": "C-1", "BJMC": "班级1"}],
            "totalSize": 101, "pageNumber": 1, "pageSize": 50,
        }}},
    )

    with pytest.raises(TimetableError, match="分页未按请求推进"):
        TimetableAPI(client).get_target_filter_options("class", "2025-2026-2")


def test_code_like_room_search_falls_back_from_name_to_code_once():
    empty = {"code": "0", "datas": {"jslb": {"rows": [], "totalSize": 0}}}
    matched = {"code": "0", "datas": {"jslb": {
        "rows": [{"CODE": "A101", "JASMC": "一号楼A101"}],
        "totalSize": 1,
    }}}
    client = Client(empty, matched)

    result = TimetableAPI(client).search_targets("room", "2025-2026-2", keyword="A101")

    assert result["items"][0]["id"] == "A101"
    assert len(client.calls) == 2
    first_rules = json.loads(client.calls[0][1]["data"]["querySetting"])
    second_rules = json.loads(client.calls[1][1]["data"]["querySetting"])
    assert first_rules[0]["name"] == "JASMC"
    assert second_rules[0]["name"] == "CODE"


def test_schedule_preserves_official_details_and_never_exposes_raw_payload():
    client = Client({
        "code": "0",
        "datas": {
            "arrangedList": [{
                "courseName": "软件工程",
                "courseCode": "COURSE-1",
                "teachClassId": "CLASS-1",
                "dayOfWeek": 3,
                "beginSection": 3,
                "endSection": 4,
                "beginTime": "10:10",
                "endTime": "11:50",
                "teachers": "教师甲,教师乙",
                "cellDetail": [{"text": "软件工程"}, {"text": "3-8周"}],
                "titleDetail": ["软件工程 COURSE-1", "3-8周 教师甲 示例校区 示例楼101", "考试 / 百分制"],
                "tags": [{"text": "必修"}],
                "color": "javascript:bad",
                "privateRemoteField": "must-not-pass-through",
            }],
            "notArrangeList": [{"courseName": "课程设计", "courseCode": "COURSE-2"}],
            "practiceList": [],
        },
    })

    result = TimetableAPI(client).get_schedule(
        mode="personal",
        term_code="2025-2026-2",
        campus_code="01",
        week=3,
    )

    course = result["courses"][0]
    assert course["weekday"] == 3
    assert course["start_section"] == 3
    assert course["teachers"] == ["教师甲", "教师乙"]
    assert course["title_details"][1].endswith("示例楼101")
    assert course["location"] == "示例校区 示例楼101"
    assert course["course_nature"] == "必修"
    assert course["assessment_type"] == "考试"
    assert course["grading_scheme"] == "百分制"
    assert course["color"] == "#2563eb"
    assert "privateRemoteField" not in str(result)
    assert client.calls[0][1]["data"]["ZC"] == 3


def test_schedule_supports_official_weeks_and_class_names_fallbacks():
    client = Client({
        "code": "0",
        "datas": {
            "arrangedList": [{
                "courseName": "软件工程",
                "dayOfWeek": 1,
                "beginSection": 1,
                "endSection": 2,
                "weeksAndTeachers": "1-8周/张三[主讲]/李四",
                "classNames": "软件2301，软件2302",
            }],
            "notArrangeList": [],
            "practiceList": [],
        },
    })

    course = TimetableAPI(client).get_schedule(
        mode="personal", term_code="2025-2026-2", campus_code="01"
    )["courses"][0]

    assert course["teachers"] == ["张三", "李四"]
    assert course["classes"] == ["软件2301", "软件2302"]


def test_mixed_official_detail_does_not_treat_experiment_group_as_week_one():
    course = TimetableAPI._course_from_row({
        "courseName": "ERP沙盘模拟",
        "dayOfWeek": 7,
        "beginSection": 1,
        "endSection": 4,
        "titleDetail": [
            {"text": "ERP沙盘模拟"},
            {"text": "2-4周 卢震 浑南校区 信息化管理实验室(文管学馆B208) 第1实验班"},
        ],
    }, 0)

    normalized = timetable_router.normalize_meeting(course, term_code="2025-2026-2")

    assert normalized.weeks == (2, 3, 4)


def test_schedule_extracts_official_room_without_a_campus_prefix():
    client = Client({"code": "0", "datas": {
        "arrangedList": [{
            "courseName": "软件工程",
            "dayOfWeek": 1,
            "beginSection": 1,
            "endSection": 2,
            "titleDetail": ["软件工程", "3-8周 教师甲 示例楼101"],
        }],
        "notArrangeList": [],
        "practiceList": [],
    }})

    course = TimetableAPI(client).get_schedule(
        mode="personal", term_code="2025-2026-2", campus_code="all"
    )["courses"][0]

    assert course["location"] == "示例楼101"


def test_missing_legacy_id_is_stable_when_remote_course_order_changes():
    first = {
        "courseName": "课程甲", "dayOfWeek": 1,
        "beginSection": 1, "endSection": 2,
    }
    second = {
        "courseName": "课程乙", "dayOfWeek": 2,
        "beginSection": 3, "endSection": 4,
    }

    before = TimetableAPI._course_from_row(first, 0)
    after = TimetableAPI._course_from_row(first, 1)

    assert before["id"] == after["id"]


def test_login_errors_are_not_relabelled_as_remote_timetable_errors():
    class LoggedOutClient:
        def post(self, *_args, **_kwargs):
            raise NEULoginError("expired")

    with pytest.raises(NEULoginError):
        TimetableAPI(LoggedOutClient()).get_weeks("2025-2026-2")


def test_malformed_or_refused_schedule_is_not_reported_as_an_empty_timetable():
    with pytest.raises(TimetableError):
        TimetableAPI(Client({"code": "500", "msg": "remote refused"})).get_schedule(
            mode="personal",
            term_code="2025-2026-2",
            campus_code="01",
        )

    with pytest.raises(TimetableError):
        TimetableAPI(Client({"code": "0", "datas": []})).get_schedule(
            mode="personal",
            term_code="2025-2026-2",
            campus_code="01",
        )


def test_http_contract_rejects_unbounded_or_untrusted_inputs():
    TimetableContextRequest(
        mode="personal",
        term_code="2025-2026-2",
        campus_code="01",
    )
    with pytest.raises(ValidationError):
        TimetableTargetSearchRequest(
            mode="teacher",
            term_code="2025-2026-2",
            keyword="x" * 101,
            page_size=51,
        )
    with pytest.raises(ValidationError):
        TimetableScheduleRequest(
            mode="personal",
            term_code="../../secret",
            campus_code="01",
            week=99,
        )


def _current_timetable_auth(term_code):
    timetable = type("Timetable", (), {
        "get_cached_terms": lambda self: [
            {"code": term_code, "name": term_code, "current": True}
        ],
        "get_terms": lambda self: [
            {"code": term_code, "name": term_code, "current": True}
        ],
    })()
    return type("Auth", (), {"username": "student", "timetable": timetable})()


def test_personal_timetable_local_cache_read_does_not_acquire_remote_session(monkeypatch):
    auth = _current_timetable_auth("2026-2027-1")
    payload = {
        "term_code": "2026-2027-1",
        "campuses": [{"code": "all", "name": "全部校区"}],
        "weeks": [],
        "sections_by_campus": {},
        "courses": [],
        "unscheduled": [],
        "practices": [],
    }
    entry = type("Entry", (), {
        "payload": payload,
        "schema_version": 3,
        "revision_algorithm_version": 1,
        "payload_type": "json",
        "saved_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
        "metadata": lambda self, **kwargs: {"is_stale": False},
    })()

    class Coordinator:
        registry = type("Registry", (), {
            "get": lambda self, resource: type("Spec", (), {
                "schema_version": 3,
                "revision_algorithm_version": 1,
                "payload_type": "json",
            })(),
        })()

        def read(self, **kwargs):
            return entry, False

        def submit(self, **kwargs):
            raise AssertionError("fresh local cache must not submit a refresh")

    def forbidden_guard():
        raise AssertionError("local cache read must not acquire the remote session guard")

    monkeypatch.setattr(timetable_router, "get_cache_coordinator", Coordinator)
    monkeypatch.setattr(timetable_router, "remote_session_guard", forbidden_guard)

    response = timetable_router.get_personal_timetable(
        term_code="2026-2027-1", refresh=False, auth=auth
    )

    assert response.term_code == "2026-2027-1"


def test_personal_timetable_typed_route_reads_the_controlled_term_variant(monkeypatch):
    payload = {
        "term_code": "2026-2027-1",
        "campuses": [{"code": "all", "name": "全部校区"}],
        "weeks": [{"number": 1, "name": "第1周"}],
        "sections_by_campus": {"all": [{"number": 1, "name": "第1节"}]},
        "courses": [],
        "unscheduled": [],
        "practices": [],
    }
    entry = type("Entry", (), {
        "payload": payload,
        "schema_version": 3,
        "revision_algorithm_version": 1,
        "payload_type": "json",
        "saved_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
        "metadata": lambda self, **kwargs: {"is_stale": kwargs["is_stale"]},
    })()

    class Coordinator:
        registry = type("Registry", (), {
            "get": lambda self, resource: type("Spec", (), {
                "schema_version": 3,
                "revision_algorithm_version": 1,
                "payload_type": "json",
            })(),
        })()

        def read(self, **kwargs):
            assert kwargs["resource"] == "personal-timetable"
            assert kwargs["variant"] == "term:2026-2027-1"
            return entry, False

    monkeypatch.setattr(timetable_router, "get_cache_coordinator", Coordinator)
    response = timetable_router.get_personal_timetable(
        term_code="2026-2027-1",
        refresh=False,
        auth=_current_timetable_auth("2026-2027-1"),
    )

    assert response.term_code == "2026-2027-1"
    assert response.is_fresh is True
    assert response.cache == {"is_stale": False}


def test_personal_timetable_does_not_return_an_incompatible_week_parser_cache(monkeypatch):
    def make_entry(schema_version, week_number):
        return type("Entry", (), {
            "payload": {
                "term_code": "2025-2026-2",
                "campuses": [{"code": "all", "name": "全部校区"}],
                "weeks": [{"number": week_number, "name": f"第{week_number}周"}],
                "sections_by_campus": {},
                "courses": [], "unscheduled": [], "practices": [],
            },
            "schema_version": schema_version,
            "revision_algorithm_version": 1,
            "payload_type": "json",
            "saved_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
            "metadata": lambda self, **kwargs: {"is_stale": kwargs["is_stale"]},
        })()

    old_entry = make_entry(1, 1)
    refreshed_entry = make_entry(3, 2)

    class Coordinator:
        registry = type("Registry", (), {
            "get": lambda self, resource: type("Spec", (), {
                "schema_version": 3,
                "revision_algorithm_version": 1,
                "payload_type": "json",
            })(),
        })()

        def __init__(self):
            self.reads = 0

        def read(self, **kwargs):
            self.reads += 1
            return (old_entry, True) if self.reads == 1 else (refreshed_entry, False)

        def submit(self, **kwargs):
            return type("Submission", (), {"job_id": "refresh-weeks"})()

    coordinator = Coordinator()
    waited = []
    monkeypatch.setattr(timetable_router, "get_cache_coordinator", lambda: coordinator)
    monkeypatch.setattr(timetable_router, "wait_for_job", waited.append)

    response = timetable_router.get_personal_timetable(
        term_code="2025-2026-2",
        refresh=False,
        auth=_current_timetable_auth("2025-2026-2"),
    )

    assert [week.number for week in response.weeks] == [2]
    assert waited == ["refresh-weeks"]


@pytest.mark.parametrize("refresh_outcome", ["failed", "cancelled", "timed_out"])
def test_personal_timetable_rejects_old_cache_when_schema_refresh_fails(
    monkeypatch, refresh_outcome
):
    old_entry = type("Entry", (), {
        "payload": {
            "term_code": "2025-2026-2",
            "campuses": [{"code": "all", "name": "全部校区"}],
            "weeks": [{"number": 1, "name": "第1周"}],
            "sections_by_campus": {},
            "courses": [], "unscheduled": [], "practices": [],
        },
        "schema_version": 1,
        "revision_algorithm_version": 1,
        "payload_type": "json",
        "saved_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
        "metadata": lambda self, **kwargs: {"is_stale": kwargs["is_stale"]},
    })()

    class Coordinator:
        registry = type("Registry", (), {
            "get": lambda self, resource: type("Spec", (), {
                "schema_version": 3,
                "revision_algorithm_version": 1,
                "payload_type": "json",
            })(),
        })()

        def read(self, **kwargs):
            return old_entry, True

        def submit(self, **kwargs):
            return type("Submission", (), {"job_id": "failed-refresh"})()

    monkeypatch.setattr(timetable_router, "get_cache_coordinator", Coordinator)
    monkeypatch.setattr(
        timetable_router,
        "wait_for_job",
        lambda job_id: type("Job", (), {"status": refresh_outcome})(),
    )

    with pytest.raises(HTTPException) as error:
        timetable_router.get_personal_timetable(
            term_code="2025-2026-2",
            refresh=False,
            auth=_current_timetable_auth("2025-2026-2"),
        )

    assert error.value.status_code == 503


def test_personal_timetable_cache_rejects_non_current_term(monkeypatch):
    class Coordinator:
        def read(self, **kwargs):
            raise AssertionError("historical term must not reach the cache coordinator")

    monkeypatch.setattr(timetable_router, "get_cache_coordinator", Coordinator)

    with pytest.raises(HTTPException) as error:
        timetable_router.get_personal_timetable(
            term_code="2025-2026-1",
            refresh=False,
            auth=_current_timetable_auth("2025-2026-2"),
        )

    assert error.value.status_code == 409
    assert "仅用于当前学期" in error.value.detail
