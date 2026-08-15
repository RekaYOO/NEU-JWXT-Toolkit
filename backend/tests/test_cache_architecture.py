from pathlib import Path

from backend.core.cache.resources import (
    canonicalize_academic_report,
    academic_report_revision_payload,
    diff_academic_report,
    avatar_bytes,
    avatar_payload,
    avatar_token,
    canonicalize_research_training,
    canonicalize_scores,
    canonicalize_personal_timetable,
    diff_personal_timetable,
    personal_timetable_term,
    personal_timetable_variant,
    diff_scores,
    score_key,
)
from backend.core.auth import AuthSessionManager
from backend.core.cache import MUTATION_POLICIES


ROOT = Path(__file__).resolve().parents[2]


def test_score_identity_and_diff_cover_change_and_removal():
    before = canonicalize_scores({
        "scores": [{
            "code": "A1", "term": "2025-1", "name": "旧名称",
            "score": "80", "gpa": 3.0, "credit": 2,
        }, {
            "code": "A2", "term": "2025-1", "name": "将删除",
            "score": "90", "gpa": 4.0, "credit": 1,
        }],
        "overall_gpa": 3.33,
    })
    after = canonicalize_scores({
        "scores": [{
            "code": "A1", "term": "2025-1", "name": "新名称",
            "score": "85", "gpa": 3.5, "credit": 2,
        }],
        "overall_gpa": 3.5,
    })
    changes = diff_scores(before, after)

    assert score_key(before["scores"][0]) == "A1\x1f2025-1"
    assert changes["counts"] == {"added": 0, "removed": 1, "changed": 1}
    assert changes["changed"][0]["before"]["name"] == "旧名称"


def test_canonical_payload_excludes_volatile_or_contact_data():
    report = canonicalize_academic_report({
        "calculated_time": "changes-every-fetch",
        "categories": [],
    })
    research = canonicalize_research_training({
        "batch": {},
        "eligibility": {},
        "topics": [{
            "topic_id": "T1",
            "title": "课题",
            "advisor_contact": "private",
            "application_reason": "private",
        }],
        "confirmed_topics": [],
    })

    assert report["calculated_time"] == ""
    assert "advisor_contact" not in research["topics"][0]
    assert "application_reason" not in research["topics"][0]


def test_academic_report_revision_ignores_remote_order_and_numeric_formatting():
    before = canonicalize_academic_report({
        "categories": [{
            "wid": "b", "name": "乙", "courses": [
                {"course_code": "B", "course_name": "乙课", "credit": 2.0},
            ], "children": [],
        }, {
            "wid": "a", "name": "甲", "courses": [
                {"course_code": "A", "course_name": "甲课", "credit": 4},
            ], "children": [],
        }],
        "credit_summary": {"total_required": 6.0},
    })
    after = canonicalize_academic_report({
        "categories": [{
            "wid": "a", "name": "甲", "courses": [
                {"course_code": "A", "course_name": "甲课", "credit": 4.0000001},
            ], "children": [],
        }, {
            "wid": "b", "name": "乙", "courses": [
                {"course_code": "B", "course_name": "乙课", "credit": 2},
            ], "children": [],
        }],
        "credit_summary": {"total_required": 6},
    })

    assert [category["wid"] for category in before["categories"]] == ["b", "a"]
    assert academic_report_revision_payload(before) == academic_report_revision_payload(after)
    assert diff_academic_report(before, after) == {
        "credit_summary_changed": False,
        "category_tree_changed": False,
        "outside_courses_changed": False,
    }


def test_avatar_binary_payload_preserves_token_and_image():
    payload = avatar_payload("token-1", b"\x89PNG-image")

    assert avatar_token(payload) == "token-1"
    assert avatar_bytes(payload) == b"\x89PNG-image"


def test_personal_timetable_variant_and_revision_payload_are_term_scoped():
    variant = personal_timetable_variant("2026-2027-1")
    assert variant == "term:2026-2027-1"
    assert personal_timetable_term(variant) == "2026-2027-1"

    before = canonicalize_personal_timetable({
        "term_code": "2026-2027-1",
        "campuses": [
            {"code": "02", "name": "示例校区"},
            {"code": "all", "name": "全部校区"},
        ],
        "weeks": [{"number": 2}, {"number": 1}],
        "sections_by_campus": {"02": [{"number": 2}, {"number": 1}]},
        "courses": [{"meeting_id": "m1", "course_name": "课程甲"}],
    })
    after = canonicalize_personal_timetable({
        **before,
        "courses": [
            {"meeting_id": "m1", "course_name": "课程甲（调整）"},
            {"meeting_id": "m2", "course_name": "课程乙"},
        ],
    })

    assert [week["number"] for week in before["weeks"]] == [1, 2]
    assert before["campuses"][0]["code"] == "all"
    assert [section["number"] for section in before["sections_by_campus"]["02"]] == [1, 2]
    changes = diff_personal_timetable(before, after)
    assert changes["added_meeting_ids"] == ["m2"]
    assert changes["changed_meeting_ids"] == ["m1"]


def test_auth_session_manager_fences_identity_and_serializes_state_cleanup():
    manager = AuthSessionManager()
    first = type("Client", (), {"username": "a"})()
    manager.set_client(first)
    epoch = manager.epoch()
    assert manager.is_current(epoch, "a")
    manager.set_client(first, force_epoch=True)
    assert manager.epoch() == epoch + 1
    epoch = manager.epoch()

    cleaned = []
    assert manager.fence_and_clear(cleaned.append) == "a"
    assert cleaned == ["a"]
    assert not manager.is_current(epoch, "a")


def test_business_layers_do_not_reintroduce_file_mtime_or_tracking_cache_writes():
    checked = [
        ROOT / "backend" / "app",
        ROOT / "backend" / "core" / "tracking",
    ]
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for directory in checked
        for path in directory.rglob("*.py")
    )
    assert "getmtime(" not in text
    assert ".stat().st_mtime" not in text

    tracking = (
        ROOT / "backend" / "core" / "tracking" / "service.py"
    ).read_text(encoding="utf-8")
    assert "auth.academic" not in tracking
    assert ".save_scores(" not in tracking


def test_cache_payload_access_stays_behind_store_or_typed_adapter():
    checked = [
        ROOT / "backend" / "app" / "routers",
        ROOT / "backend" / "core" / "tracking",
        ROOT / "backend" / "core" / "academic",
    ]
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for directory in checked
        for path in directory.rglob("*.py")
    )
    assert ".commit_success(" not in text
    assert "INSERT INTO cache_entries" not in text
    assert "UPDATE cache_entries" not in text
    assert "get_scores_smart(" not in text
    assert "get_report_smart(" not in text


def test_remote_routes_use_the_shared_session_dependency():
    routers = ROOT / "backend" / "app" / "routers"
    all_routes = "\n".join(
        path.read_text(encoding="utf-8")
        for path in routers.glob("*.py")
    )
    assert "Depends(require_auth)" not in all_routes
    assert "Depends(get_auth_client)" not in all_routes
    for module in ("evaluation.py", "experiment.py", "exam.py", "timetable.py"):
        text = (routers / module).read_text(encoding="utf-8")
        assert "Depends(require_serialized_auth)" in text
    research = (routers / "research.py").read_text(encoding="utf-8")
    assert research.count("Depends(require_serialized_auth)") >= 5
    auth = (routers / "auth.py").read_text(encoding="utf-8")
    assert "with remote_session_guard():" in auth


def test_remote_mutations_declare_no_retry_and_consistency_action():
    expected = {
        "research.enroll",
        "research.cancel",
        "experiment.select",
        "experiment.deselect",
        "evaluation.submit",
        "evaluation.batch",
        "jwxk.confirm",
        "jwxk.select",
        "jwxk.deselect",
    }
    assert set(MUTATION_POLICIES) == expected
    assert all(
        policy.automatic_retry is False
        and (policy.invalidations or policy.refetches)
        for policy in MUTATION_POLICIES.values()
    )
    route_sources = "\n".join(
        (ROOT / "backend" / "app" / "routers" / name).read_text(
            encoding="utf-8"
        )
        for name in ("research.py", "experiment.py", "evaluation.py", "course_selection.py")
    )
    assert all(
        f'mutation_policy("{operation}")' in route_sources
        for operation in expected
    )


def test_authoritative_state_is_not_registered_as_cache():
    dependencies = (
        ROOT / "backend" / "app" / "dependencies.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "grade-tracking",
        "gpa-simulation",
        "research-favorites",
        "credentials",
        "session",
        "logs",
    )
    registered_blocks = dependencies.split("_cache_registry = CacheRegistry", 1)[1]
    registered_blocks = registered_blocks.split("_cache_store =", 1)[0]
    assert all(
        f'resource="{resource}"' not in registered_blocks
        for resource in forbidden
    )
