import json
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.core.course_selection import CourseSelectionAutomationService, JwxkError
from backend.core.course_selection.jwxk import JwxkRateLimitError


def _service(tmp_path):
    return CourseSelectionAutomationService(
        tmp_path,
        auth_provider=lambda: None,
        client_builder=lambda auth: None,
    )


def test_automation_tasks_are_account_scoped_and_require_explicit_start(tmp_path):
    service = _service(tmp_path)
    task = service.create("student-a", {
        "batch_code": "batch", "term_code": "2026-2027-1",
        "name": "目标课程", "items": [], "poll_seconds": 15,
    })
    assert task["status"] == "draft"
    assert service.list("student-b") == []
    running = service.action("student-a", task["task_id"], "start")
    assert running["status"] == "running"


def test_automation_tasks_are_filtered_by_batch(tmp_path):
    service = _service(tmp_path)
    first = service.create("student", {
        "batch_code": "batch-1", "term_code": "2026-2027-1",
        "name": "第一轮任务", "items": [], "poll_seconds": 15,
    })
    service.create("student", {
        "batch_code": "batch-2", "term_code": "2026-2027-1",
        "name": "第二轮任务", "items": [], "poll_seconds": 15,
    })

    assert [item["task_id"] for item in service.list("student", "batch-1")] == [first["task_id"]]
    assert len(service.list("student")) == 2


def test_weight_task_immediate_check_is_queued_and_uses_longer_schedule(tmp_path):
    service = _service(tmp_path)
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1",
        "name": "实时策略", "task_type": "weight_strategy",
        "grade_size": 100, "rebalance_seconds": 30,
        "groups": [], "items": [],
    })
    service.action("student", task["task_id"], "start")

    queued = service.action("student", task["task_id"], "check_now")
    snapshot = service.list("student", "batch")[0]

    assert queued["manual_check_requested_at"]
    assert snapshot["next_attempt_at"] is not None
    # Strategy tasks use the new 30-minute default/fallback even when a
    # legacy direct-service payload supplies a shorter interval.
    assert snapshot["poll_interval_seconds"] == 1800
    assert service._consume_manual_check(service._tasks[0]) is True


def test_weight_task_snapshot_uses_round_level_rebalance_setting(tmp_path):
    service = _service(tmp_path)
    service.update_automation_settings("student", "batch", {"rebalance_seconds": 1800})
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1",
        "name": "实时策略", "task_type": "weight_strategy",
        "grade_size": 100, "rebalance_seconds": 60,
        "groups": [{"group_id": "g1", "name": "组1", "target_count": 1}],
        "items": [{"class_id": "c1", "course_code": "C1", "plan_group_id": "g1"}],
    })
    assert service.list("student", "batch")[0]["poll_interval_seconds"] == 1800


def test_saved_plan_changes_sync_into_bound_tasks(tmp_path):
    service = _service(tmp_path)
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1",
        "name": "组1任务", "task_type": "weight_strategy",
        "grade_size": 100,
        "groups": [{"group_id": "g1", "name": "组1", "target_count": 1}],
        "items": [{"class_id": "c1", "course_code": "C1", "plan_group_id": "g1"}],
    })
    changed = service.sync_bound_plan(
        "student", "batch",
        groups=[{"group_id": "g1", "name": "组1改名", "target_count": 2}],
        items=[
            {"class_id": "c1", "course_code": "C1", "plan_group_id": "g1"},
            {"class_id": "c2", "course_code": "C2", "plan_group_id": "g1"},
        ],
    )
    assert changed == 1
    synced = service.list("student", "batch")[0]
    assert synced["groups"][0]["name"] == "组1改名"
    assert synced["groups"][0]["target_count"] == 2
    assert {item["class_id"] for item in synced["items"]} == {"c1", "c2"}
    assert synced["bound_group_ids"] == ["g1"]


def test_confirmed_round_time_change_updates_unfinished_tasks_and_archive(tmp_path):
    service = _service(tmp_path)
    old_start = (datetime.now().astimezone() + timedelta(hours=1)).isoformat()
    old_end = (datetime.now().astimezone() + timedelta(hours=3)).isoformat()
    new_start = (datetime.now().astimezone() + timedelta(hours=2)).isoformat()
    new_end = (datetime.now().astimezone() + timedelta(hours=5)).isoformat()
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1",
        "name": "实时策略", "task_type": "weight_strategy", "grade_size": 100,
        "groups": [], "items": [], "start_at": old_start, "end_at": old_end,
    })
    service.action("student", task["task_id"], "start")
    service._tasks[0]["weight_status"].update({
        "final_5_executed": True, "final_3_executed": True,
        "final_rebalance_executed": True,
    })
    service.merge_catalog_archive("student", batch={
        "code": "batch", "name": "轮次", "term_code": "2026-2027-1",
        "selection_type_code": "04", "begin_time": old_start, "end_time": old_end,
    }, scope="ROUND", groups=[])

    result = service.sync_batch_times(
        "student", "batch", start_at=new_start, end_at=new_end,
    )

    assert result["changed_task_count"] == 1
    synced = service.list("student", "batch")[0]
    assert synced["start_at"] == new_start
    assert synced["end_at"] == new_end
    assert synced["status"] == "waiting"
    assert "final_5_executed" not in service._tasks[0]["weight_status"]
    archive = service.get_catalog_archive_view("student", "batch")
    assert archive["begin_time"] == new_start
    assert archive["end_time"] == new_end

    restored = _service(tmp_path)
    restored_task = restored.list("student", "batch")[0]
    assert restored_task["start_at"] == new_start
    assert restored_task["end_at"] == new_end


def test_catalog_sync_does_not_block_live_task_scheduler(tmp_path):
    service = _service(tmp_path)
    catalog_started = threading.Event()
    release_catalog = threading.Event()
    task_ran = threading.Event()

    def slow_catalog_sync():
        catalog_started.set()
        release_catalog.wait(timeout=2)

    service._tick_catalog_sync = slow_catalog_sync
    service._tick_catalog_archives = lambda: None
    service._tick = lambda _task: task_ran.set()
    service.start()
    try:
        assert catalog_started.wait(timeout=1)
        task = service.create("student", {
            "batch_code": "batch", "term_code": "2026-2027-1",
            "name": "实时策略", "items": [], "poll_seconds": 15,
        })
        service.action("student", task["task_id"], "start")
        assert task_ran.wait(timeout=1)
    finally:
        release_catalog.set()
        service.stop()


def test_running_task_snapshot_exposes_poll_progress_without_sharing_mutable_state(tmp_path):
    service = _service(tmp_path)
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1",
        "name": "实时任务", "items": [{
            "class_id": "class-1", "course_code": "A", "course_name": "课程A",
            "schedules": [{"weekday": 1}], "official_schedule": "完整安排",
        }], "poll_seconds": 15,
    })
    service.action("student", task["task_id"], "start")
    stored = service._tasks[0]
    stored["last_attempt_at"] = datetime.now().astimezone().isoformat()
    stored["attempt_count"] = 3

    snapshot = service.list("student", "batch")[0]

    assert snapshot["poll_interval_seconds"] == 15
    assert snapshot["next_attempt_at"] is not None
    assert snapshot["attempt_count"] == 3
    assert "schedules" not in snapshot["items"][0]
    snapshot["results"].append({"message": "frontend-only"})
    assert stored["results"] == []


def test_task_course_refresh_prefers_archived_real_scope_and_records_live_counts(tmp_path):
    calls = []

    class Client:
        def search_courses(self, **kwargs):
            calls.append(kwargs)
            return {"courses": [{
                "class_id": "class-1", "course_code": "A", "course_name": "课程A",
                "capacity": 50, "weight_participant_count": 12,
                "market_participant_count": 12, "market_participant_label": "已投注人数",
            }]}

    service = _service(tmp_path)
    service.merge_catalog_archive(
        "student",
        batch={"code": "batch", "selection_type_code": "04"},
        scope="TJKC",
        groups=[{"group_id": "A", "classes": [{
            "class_id": "class-1", "course_code": "A", "course_name": "课程A",
            "teaching_class_type": "TJKC", "source_scopes": ["ROUND", "TJKC"],
        }]}],
    )
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1", "name": "实时任务",
        "items": [{
            "class_id": "class-1", "course_code": "A", "course_name": "课程A",
            "teaching_class_type": "ROUND",
        }],
    })

    live = service._refresh_task_course_states(Client(), task)

    assert calls[0]["teaching_class_type"] == "TJKC"
    assert live["class-1"]["weight_participant_count"] == 12
    assert task["course_states"]["class-1"]["market_participant_count"] == 12


def test_weight_strategy_reuses_just_completed_market_snapshot_without_duplicate_queries(tmp_path):
    class Client:
        def search_courses(self, **_kwargs):
            raise AssertionError("fresh complete market snapshot must be reused")

    service = _service(tmp_path)
    service.merge_catalog_archive(
        "student",
        batch={"code": "batch", "selection_type_code": "04"},
        scope="TJKC",
        groups=[{"group_id": "A", "classes": [{
            "class_id": "class-1", "course_code": "A", "course_name": "课程A",
            "teaching_class_type": "TJKC", "capacity": 50,
            "weight_participant_count": 27, "market_participant_count": 27,
        }]}],
    )
    snapshot_at = datetime.now().astimezone().isoformat()
    with service._lock:
        service._archives[0].update({
            "sync_status": "complete", "last_sync_at": snapshot_at,
        })
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1",
        "name": "实时策略", "task_type": "weight_strategy", "grade_size": 100,
        "groups": [{"group_id": "g", "name": "目标", "target_count": 1}],
        "items": [{
            "plan_group_id": "g", "class_id": "class-1",
            "course_code": "A", "course_name": "课程A",
            "teaching_class_type": "TJKC",
        }],
    })
    task.setdefault("weight_status", {})["market_snapshot_at"] = snapshot_at

    live = service._refresh_task_course_states(Client(), task)

    assert live["class-1"]["weight_participant_count"] == 27
    assert any(
        "复用刚完成的完整市场快照" in item["message"]
        for item in task["execution"]["events"]
    )


def test_task_course_refresh_ignores_fuzzy_rows_and_requires_exact_class(tmp_path):
    class Client:
        def search_courses(self, **_kwargs):
            return {"courses": [
                {"class_id": "wrong", "course_code": "A-OTHER", "weight_participant_count": 99},
                {"class_id": "class-1", "course_code": "A", "weight_participant_count": 12},
            ]}

    service = _service(tmp_path)
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1", "name": "实时任务",
        "items": [{
            "class_id": "class-1", "course_code": "A", "course_name": "课程A",
            "teaching_class_type": "TJKC",
        }],
    })

    live = service._refresh_task_course_states(Client(), task)

    assert set(live) == {"class-1"}
    assert any("返回 2 条，精确命中 1/1" in item["message"] for item in task["execution"]["events"])


def test_task_course_refresh_stops_when_exact_class_is_missing(tmp_path):
    class Client:
        def search_courses(self, **_kwargs):
            return {"courses": [{"class_id": "wrong", "course_code": "A"}]}

    service = _service(tmp_path)
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1", "name": "实时任务",
        "items": [{
            "class_id": "class-1", "course_code": "A", "course_name": "课程A",
            "teaching_class_type": "TJKC",
        }],
    })

    with pytest.raises(JwxkError, match="未找到方案中的教学班"):
        service._refresh_task_course_states(Client(), task)


def test_cancelled_automation_task_is_removed_immediately(tmp_path):
    service = _service(tmp_path)
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1",
        "name": "待取消任务", "items": [], "poll_seconds": 15,
    })

    result = service.action("student", task["task_id"], "cancel")

    assert result["status"] == "cancelled"
    assert service.list("student") == []


def test_running_automation_task_resumes_with_reconciliation_after_restart(tmp_path):
    path = tmp_path / "course_selection_tasks.json"
    path.write_text(json.dumps([{
        "task_id": "task-1", "account": "student", "status": "running",
        "batch_code": "batch", "term_code": "2026-2027-1", "items": [],
    }]), encoding="utf-8")

    task = _service(tmp_path).list("student")[0]
    assert task["status"] == "waiting"
    assert task["desired_state"] == "running"
    assert task["restart_reconcile"] is True
    assert "核验官方状态" in task["message"]


def test_pre_start_task_uses_opening_burst_then_degrades_to_vacancy_watch(tmp_path):
    class Client:
        def get_selected(self, **_kwargs):
            return {"selected": [], "volunteered": []}

        def search_courses(self, **_kwargs):
            return {"courses": [{"class_id": "class-1", "full": True}]}

    auth = SimpleNamespace(is_logged_in=True, username="student")
    service = CourseSelectionAutomationService(
        tmp_path, auth_provider=lambda: auth, client_builder=lambda _auth: Client(),
    )
    future = (datetime.now().astimezone() + timedelta(minutes=5)).isoformat()
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1", "name": "开场任务",
        "start_at": future, "poll_seconds": 15, "groups": [
            {"group_id": "group", "name": "目标", "target_count": 1},
        ], "items": [{
            "plan_group_id": "group", "priority": 1, "course_code": "A",
            "course_name": "A", "class_id": "class-1",
        }],
    })
    assert task["polling_mode"] == "opening_burst"
    running = service.action("student", task["task_id"], "start")
    assert running["status"] == "waiting"
    task["start_at"] = (datetime.now().astimezone() - timedelta(seconds=1)).isoformat()
    task["status"] = "running"
    task["desired_state"] = "running"
    task["last_attempt_at"] = None

    service._tick(task)

    assert task["polling_mode"] == "vacancy_watch"
    assert service._poll_interval(task) == 15


def test_login_recovery_provider_continues_read_phase_without_pausing(tmp_path):
    auth = SimpleNamespace(is_logged_in=True, username="student")

    class Client:
        def get_selected(self, **_kwargs):
            return {"selected": [], "volunteered": []}

    service = CourseSelectionAutomationService(
        tmp_path,
        auth_provider=lambda: None,
        auth_recover_provider=lambda: auth,
        client_builder=lambda _auth: Client(),
    )
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1", "name": "恢复登录",
        "groups": [], "items": [], "poll_seconds": 15,
    })
    task.update({"status": "waiting", "desired_state": "running"})

    service._tick(task)

    assert task["status"] == "running"
    assert task["attempt_count"] == 1


def test_jwxk_task_does_not_reject_same_account_when_primary_flag_is_false(tmp_path):
    auth = SimpleNamespace(is_logged_in=False, username="student", password="")
    service = CourseSelectionAutomationService(
        tmp_path,
        auth_provider=lambda: auth,
        auth_recover_provider=lambda: auth,
        client_builder=lambda _auth: None,
    )

    assert service._task_auth({"account": "student"}) is auth
    assert service._task_auth({"account": "another-student"}) is None


def test_inflight_write_after_restart_is_reconciled_not_replayed(tmp_path):
    class Client:
        selected_calls = 0

        def get_selected(self, **_kwargs):
            return {"selected": [], "volunteered": []}

        def select_course(self, **_kwargs):
            self.selected_calls += 1
            raise AssertionError("uncertain write must never be replayed")

    client = Client()
    auth = SimpleNamespace(is_logged_in=True, username="student")
    service = CourseSelectionAutomationService(
        tmp_path, auth_provider=lambda: auth, client_builder=lambda _auth: client,
    )
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1", "name": "待核验",
        "groups": [{"group_id": "group", "name": "目标", "target_count": 1}],
        "items": [{"plan_group_id": "group", "course_code": "A", "class_id": "class-1"}],
        "poll_seconds": 15,
    })
    task.update({
        "status": "waiting", "desired_state": "running", "last_attempt_at": None,
        "inflight_mutation": {"action": "select", "class_id": "class-1", "course_code": "A"},
    })

    service._tick(task)

    assert task["status"] == "needs_review"
    assert client.selected_calls == 0


def test_legacy_cancelled_tasks_are_removed_on_restart(tmp_path):
    path = tmp_path / "course_selection_tasks.json"
    path.write_text(json.dumps([{
        "task_id": "cancelled", "account": "student", "status": "cancelled",
        "batch_code": "batch", "term_code": "2026-2027-1", "items": [],
    }]), encoding="utf-8")

    assert _service(tmp_path).list("student") == []


def test_catalog_archives_are_account_scoped_and_only_deleted_explicitly(tmp_path):
    service = _service(tmp_path)
    archive = service.merge_catalog_archive(
        "student-a",
        batch={
            "code": "batch", "name": "必修课初选", "term_code": "2026-2027-1",
            "term_name": "2026-2027学年秋季学期", "begin_time": "2026-08-14 08:00:00",
            "end_time": "2026-08-14 18:00:00",
        },
        scope="FANKC",
        groups=[{
            "group_id": "course-a", "source_tags": ["全校课程查询"],
            "classes": [{
                "class_id": "class-a", "course_code": "A", "course_name": "课程A",
                "capacity": 30, "selected_count": 20, "eligibility_status": "unknown",
            }],
        }],
    )

    service.update_archive_eligibility(
        "student-a", batch_code="batch",
        results=[{"class_id": "class-a", "status": "selectable", "reason": ""}],
    )
    assert service.list_catalog_archives("student-b") == []
    saved = service.list_catalog_archives("student-a")[0]
    assert saved["courses"][0]["eligibility_status"] == "selectable"
    assert saved["courses"][0]["capacity_updated_at"]
    assert service.delete_catalog_archive("student-b", archive["archive_id"]) is False
    assert service.list_catalog_archives("student-a")
    assert service.delete_catalog_archive("student-a", archive["archive_id"]) is True
    assert service.list_catalog_archives("student-a") == []


def test_complete_catalog_sync_excludes_global_directory_scope(tmp_path):
    batch = SimpleNamespace(
        code="batch",
        menus=({"code": "FANKC"}, {"code": "XGKC"}, {"code": "ALLKC"}),
        to_dict=lambda: {
            "code": "batch", "name": "轮次", "term_code": "2026-2027-1",
            "menus": [{"code": "FANKC"}, {"code": "XGKC"}, {"code": "ALLKC"}],
        },
    )

    class FakeClient:
        def get_context(self):
            return {"batches": [batch]}

        def search_courses(self, *, teaching_class_type, **_kwargs):
            return {"total": 1, "courses": [{
                "class_id": f"{teaching_class_type}-1",
                "course_code": teaching_class_type,
                "course_name": teaching_class_type,
            }]}

    auth = SimpleNamespace(is_logged_in=True, username="student")
    service = CourseSelectionAutomationService(
        tmp_path, auth_provider=lambda: auth, client_builder=lambda _auth: FakeClient(),
    )
    service.schedule_catalog_sync("student", batch={"code": "batch", "name": "轮次"})
    service._tick_catalog_sync()

    saved = service.list_catalog_archives("student")[0]
    assert saved["sync_status"] == "complete"
    assert saved["catalog_complete"] is True
    assert saved["sync_scopes"] == ["FANKC", "XGKC"]
    assert {course["course_code"] for course in saved["courses"]} == {"FANKC", "XGKC"}
    assert saved["scope_options"] == [
        {"code": "FANKC", "name": "FANKC"},
        {"code": "XGKC", "name": "XGKC"},
        {"code": "ALLKC", "name": "ALLKC"},
    ]


def test_catalog_archive_persists_menu_metadata_without_global_directory_rows(tmp_path):
    service = _service(tmp_path)
    service.merge_catalog_archive(
        "student",
        batch={
            "code": "batch", "name": "轮次",
            "menus": [
                {"code": "TJKC", "name": "任务推荐班课程"},
                {"code": "ALLKC", "name": "全校课程查询"},
            ],
        },
        scope="TJKC",
        groups=[{
            "group_id": "A", "classes": [{
                "class_id": "class-a", "course_code": "A", "course_name": "推荐课",
                "teaching_class_type": "TJKC",
            }],
        }],
    )

    saved = service.list_catalog_archives("student")[0]
    assert saved["scope_options"] == [
        {"code": "TJKC", "name": "任务推荐班课程"},
        {"code": "ALLKC", "name": "全校课程查询"},
    ]
    assert all(course["teaching_class_type"] != "ALLKC" for course in saved["courses"])


def test_complete_catalog_is_requeued_only_after_dynamic_refresh_interval(tmp_path):
    service = _service(tmp_path)
    service.merge_catalog_archive(
        "student", batch={"code": "batch", "name": "轮次"}, scope="FANKC", groups=[],
    )
    with service._lock:
        archive = service._archives[0]
        archive["sync_status"] = "complete"
        archive["catalog_complete"] = True
        archive["last_sync_at"] = datetime.now().astimezone().isoformat()

    service.schedule_catalog_sync("student", batch={"code": "batch", "name": "轮次"})
    assert service.list_catalog_archives("student")[0]["sync_status"] == "complete"

    with service._lock:
        service._archives[0]["last_sync_at"] = (
            datetime.now().astimezone() - timedelta(minutes=9)
        ).isoformat()
    service.schedule_catalog_sync("student", batch={"code": "batch", "name": "轮次"})
    assert service.list_catalog_archives("student")[0]["sync_status"] == "complete"

    with service._lock:
        service._archives[0]["last_sync_at"] = (
            datetime.now().astimezone() - timedelta(minutes=11)
        ).isoformat()
    service.schedule_catalog_sync("student", batch={"code": "batch", "name": "轮次"})
    refreshed = service.list_catalog_archives("student")[0]
    assert refreshed["sync_status"] == "queued"
    assert refreshed["catalog_complete"] is True


def test_forced_catalog_sync_queues_a_fresh_market_snapshot_immediately(tmp_path):
    service = _service(tmp_path)
    service.merge_catalog_archive(
        "student", batch={"code": "batch", "name": "轮次"}, scope="TJKC", groups=[],
    )
    with service._lock:
        archive = service._archives[0]
        archive["sync_status"] = "complete"
        archive["catalog_complete"] = True
        archive["last_sync_at"] = datetime.now().astimezone().isoformat()

    service.schedule_catalog_sync(
        "student", batch={"code": "batch", "name": "轮次"}, force=True,
    )

    assert service.list_catalog_archives("student")[0]["sync_status"] == "queued"


def test_failed_catalog_sync_is_silently_requeued_with_clean_progress(tmp_path):
    auth = SimpleNamespace(username="student", is_logged_in=False)
    service = CourseSelectionAutomationService(
        tmp_path,
        auth_provider=lambda: None,
        auth_recover_provider=lambda: auth,
        client_builder=lambda _auth: None,
    )
    service.merge_catalog_archive(
        "student", batch={"code": "batch", "name": "轮次"}, scope="TJKC", groups=[],
    )
    with service._lock:
        archive = service._archives[0]
        archive.update({
            "sync_status": "failed", "sync_loaded": 204, "sync_total": 204,
            "sync_retry_at": (datetime.now().astimezone() - timedelta(seconds=1)).isoformat(),
        })

    service._requeue_failed_catalog_syncs()

    refreshed = service.list_catalog_archives("student")[0]
    assert refreshed["sync_status"] == "queued"
    assert refreshed["sync_loaded"] == 0
    assert refreshed["sync_total"] == 0


def test_catalog_rate_limit_uses_official_cooldown_instead_of_auth_retry(tmp_path):
    service = _service(tmp_path)
    service.merge_catalog_archive(
        "student", batch={"code": "batch", "name": "轮次"}, scope="TJKC", groups=[],
    )

    service._set_catalog_sync_state(
        "student", "batch", "failed", "学校限制请求频率",
        retry_after_seconds=75,
    )

    archive = service.list_catalog_archives("student")[0]
    retry_at = datetime.fromisoformat(archive["sync_retry_at"]).astimezone()
    remaining = (retry_at - datetime.now().astimezone()).total_seconds()
    assert archive["sync_status"] == "failed"
    assert 70 <= remaining <= 75


def test_catalog_watchdog_resets_a_scan_without_progress(tmp_path):
    service = _service(tmp_path)
    service.merge_catalog_archive(
        "student", batch={"code": "batch", "name": "轮次"}, scope="TJKC", groups=[],
    )
    with service._lock:
        archive = service._archives[0]
        archive.update({
            "sync_status": "running",
            "updated_at": (datetime.now().astimezone() - timedelta(minutes=3)).isoformat(),
        })

    service._reset_stale_catalog_syncs()

    refreshed = service.list_catalog_archives("student")[0]
    assert refreshed["sync_status"] == "failed"
    assert "自动重置" in refreshed["sync_error"]
    assert refreshed["sync_retry_at"]


def test_union_catalog_archive_merges_sources_filters_and_paginates(tmp_path):
    service = _service(tmp_path)
    batch = {"code": "batch", "name": "轮次", "term_code": "2026-2027-1"}
    service.merge_catalog_archive(
        "student", batch=batch, scope="FANKC", groups=[{
            "group_id": "A", "source_tags": ["培养方案内课"], "classes": [{
                "class_id": "class-a", "course_code": "A", "course_name": "人文课程",
                "credits": "2", "course_category": "人文社会科学类",
                "course_nature": "选修", "eligibility_status": "selectable",
            }],
        }],
    )
    service.merge_catalog_archive(
        "student", batch=batch, scope="ALLKC", groups=[{
            "group_id": "A", "source_tags": ["全校课程查询"], "classes": [{
                "class_id": "class-a", "course_code": "A", "course_name": "人文课程",
                "credits": "2", "course_category": "人文社会科学类",
                "course_nature": "选修", "eligibility_status": "selectable",
            }],
        }, {
            "group_id": "B", "source_tags": ["全校课程查询"], "classes": [{
                "class_id": "class-b", "course_code": "B", "course_name": "其他课程",
                "credits": "1", "course_category": "自然科学类",
                "course_nature": "选修", "eligibility_status": "unavailable",
            }],
        }],
    )

    result = service.query_catalog_archive(
        "student", batch_code="batch", page_number=1, page_size=20,
        filters={"KCLB": "人文社会科学类"},
    )

    assert result["total"] == 1
    assert result["groups"][0]["course_code"] == "A"
    assert result["groups"][0]["source_tags"] == ["培养方案内课"]
    assert result["groups"][0]["classes"][0]["teaching_class_type"] == "FANKC"

    merged = service.query_catalog_archive(
        "student", batch_code="batch", page_number=1, page_size=20, scope="ALL",
    )
    assert {group["course_code"] for group in merged["groups"]} == {"A"}
    assert merged["groups"][0]["source_tags"] == ["培养方案内课"]
    assert merged["groups"][0]["classes"][0]["source_scopes"] == ["FANKC"]

    all_school = service.query_catalog_archive(
        "student", batch_code="batch", page_number=1, page_size=20, scope="ALLKC",
    )
    assert all_school["total"] == 2
    assert {group["course_code"] for group in all_school["groups"]} == {"A", "B"}


def test_all_school_directory_rows_are_preserved_on_restart(tmp_path):
    path = tmp_path / "course_selection_catalog_history.json"
    path.write_text(json.dumps([{
        "archive_id": "archive", "account": "student", "batch_code": "batch",
        "sync_scopes": ["TJKC", "ALLKC"], "sync_loaded": 4001, "sync_total": 4001,
        "courses": [{
            "class_id": "recommended", "course_code": "A", "course_name": "推荐课",
            "teaching_class_type": "TJKC", "source_scopes": ["TJKC", "ALLKC"],
            "source_tags": ["任务推荐班课程", "全校课程查询"],
        }, {
            "class_id": "directory-only", "course_code": "B", "course_name": "目录课",
            "teaching_class_type": "ALLKC", "source_scopes": ["ALLKC"],
            "source_tags": ["全校课程查询"],
        }],
    }]), encoding="utf-8")

    service = _service(tmp_path)
    archive = service.list_catalog_archives("student")[0]

    assert {course["class_id"] for course in archive["courses"]} == {
        "recommended", "directory-only",
    }
    recommended = next(course for course in archive["courses"] if course["class_id"] == "recommended")
    assert recommended["source_scopes"] == ["TJKC", "ALLKC"]
    assert recommended["source_tags"] == ["任务推荐班课程", "全校课程查询"]
    assert archive["sync_scopes"] == ["TJKC", "ALLKC"]

    round_catalog = service.query_catalog_archive(
        "student", batch_code="batch", page_number=1, page_size=20, scope="ROUND",
    )
    assert {group["course_code"] for group in round_catalog["groups"]} == {"A"}
    all_school = service.query_catalog_archive(
        "student", batch_code="batch", page_number=1, page_size=20, scope="ALLKC",
    )
    assert {group["course_code"] for group in all_school["groups"]} == {"A", "B"}


def test_all_school_exact_query_cache_survives_restart(tmp_path):
    service = _service(tmp_path)
    service.merge_catalog_archive(
        "student",
        batch={"code": "batch", "name": "轮次"},
        scope="ALLKC",
        groups=[],
        query_key="exact-query",
        query_result={
            "total": 1, "scope": "ALLKC", "scope_options": [],
            "groups": [{"group_id": "A", "course_name": "缓存课程", "classes": []}],
        },
    )

    restored = _service(tmp_path).get_catalog_query(
        "student", batch_code="batch", query_key="exact-query",
    )

    assert restored is not None
    assert restored["groups"][0]["course_name"] == "缓存课程"


def test_archive_filters_match_campus_code_name_and_category_aliases(tmp_path):
    service = _service(tmp_path)
    service.merge_catalog_archive(
        "student", batch={"code": "batch", "name": "轮次"}, scope="TJKC", groups=[{
            "group_id": "A", "classes": [{
                "class_id": "class-a", "course_code": "A", "course_name": "工商管理课程",
                "course_category": "专业方向类", "department": "工商管理学院",
                "campus": "01", "campus_name": "浑南校区", "schedules": [],
            }],
        }],
    )

    result = service.query_catalog_archive(
        "student", batch_code="batch", page_number=1, page_size=20,
        campus="浑南校区", filters={"KCLB": "专业方向课"},
    )

    assert result["total"] == 1
    assert result["groups"][0]["department"] == "工商管理学院"

    recommended = service.query_catalog_archive(
        "student", batch_code="batch", page_number=1, page_size=20,
        scope="TJKC", campus="01", filters={"KCLB": "专业方向类"},
    )
    unrelated = service.query_catalog_archive(
        "student", batch_code="batch", page_number=1, page_size=20,
        scope="XGKC", filters={"KCLB": "专业方向类"},
    )

    assert recommended["total"] == 1
    assert recommended["groups"][0]["classes"][0]["teaching_class_type"] == "TJKC"
    assert unrelated["total"] == 0


def test_catalog_final_refresh_updates_saved_selectable_course_counts(tmp_path):
    class FakeClient:
        def search_courses(self, **kwargs):
            assert kwargs["batch_code"] == "batch"
            assert kwargs["keyword"] == "A"
            return {"courses": [{
                "class_id": "class-a", "course_code": "A", "course_name": "课程A",
                "capacity": 30, "selected_count": 29, "full": False,
                "first_choice_count": 28, "weight_participant_count": 0,
            }]}

    auth = SimpleNamespace(is_logged_in=True, username="student")
    service = CourseSelectionAutomationService(
        tmp_path,
        auth_provider=lambda: auth,
        client_builder=lambda _auth: FakeClient(),
    )
    archive = service.merge_catalog_archive(
        "student",
        batch={
            "code": "batch", "name": "初选", "term_code": "2026-2027-1",
            "end_time": "2026-08-14 18:00:00",
        },
        scope="TJKC",
        groups=[{"group_id": "A", "classes": [{
            "class_id": "class-a", "course_code": "A", "course_name": "课程A",
            "capacity": 30, "selected_count": 10, "eligibility_status": "selectable",
        }]}],
    )

    service._refresh_archive_counts(auth, archive)
    saved = service.list_catalog_archives("student")[0]
    assert saved["courses"][0]["selected_count"] == 29
    assert saved["final_refresh_status"] == "complete"
    assert saved["final_refresh_at"]


def test_underfilled_warning_only_lists_current_weighted_courses(tmp_path):
    messages = []

    class FakeClient:
        def get_selected(self, **_kwargs):
            return {
                "selected": [],
                "volunteered": [{
                    "class_id": "weighted", "course_code": "W1",
                    "course_name": "已投课程", "devoted_weight": 20,
                }],
            }

    auth = SimpleNamespace(is_logged_in=True, username="student")
    service = CourseSelectionAutomationService(
        tmp_path,
        auth_provider=lambda: auth,
        client_builder=lambda _auth: FakeClient(),
        notification_provider=lambda subject, body, key: messages.append((subject, body, key)) or True,
    )
    archive = service.merge_catalog_archive(
        "student",
        batch={
            "code": "batch", "name": "权重轮次", "term_code": "2026-2027-1",
            "selection_type_code": "04",
        },
        scope="TJKC",
        groups=[{"group_id": "catalog", "classes": [{
            "class_id": "weighted", "course_code": "W1", "course_name": "已投课程",
            "capacity": 30, "weight_participant_count": 8,
        }, {
            "class_id": "unrelated", "course_code": "U1", "course_name": "未投课程",
            "capacity": 50, "weight_participant_count": 3,
        }]}],
    )
    service.update_automation_settings("student", "batch", {
        "mail_enabled": True, "notify_underfilled_warning": True,
    })

    service._notify_underfilled_warning("student", archive, auth)
    service._notify_underfilled_warning("student", archive, auth)

    assert len(messages) == 1
    subject, body, _ = messages[0]
    assert subject == "JWXK 已投权课程开课风险提示"
    assert "已投课程 - 8/30" in body
    assert "未投课程" not in body


def test_weight_strategy_recalculates_live_bidders_then_starts_safe_rebalance(tmp_path):
    class FakeClient:
        def __init__(self):
            self.volunteered = [{
                "class_id": "class-a", "course_code": "A", "course_name": "课程A",
                "devoted_weight": 20,
            }]
            self.dropped = []

        def get_selected(self, **_kwargs):
            # Some JWXK rounds expose the same pending weighted course in both
            # result feeds.  It must remain a managed recommendation instead
            # of being mistaken for an already-finalized course.
            return {"selected": list(self.volunteered), "volunteered": list(self.volunteered)}

        def search_courses(self, *, keyword, **_kwargs):
            rows = {
                "A": {"class_id": "class-a", "course_code": "A", "course_name": "课程A", "capacity": 30, "weight_participant_count": 40},
                "B": {"class_id": "class-b", "course_code": "B", "course_name": "课程B", "capacity": 30, "weight_participant_count": 35},
            }
            return {"courses": [rows[keyword]]}

        def get_weight_budget(self, **_kwargs):
            return {"remaining": 85, "total": 105, "used": 20, "minimum": 5, "step": 1}

        def deselect_course(self, *, class_id, **_kwargs):
            self.dropped.append(class_id)
            self.volunteered = [
                item for item in self.volunteered if item["class_id"] != class_id
            ]
            return {"success": True, "code": "200", "message": "queued"}

        def select_course(self, *, class_id, course_code, weight, **_kwargs):
            self.volunteered.append({
                "class_id": class_id, "course_code": course_code,
                "course_name": "课程B", "devoted_weight": weight,
            })
            return {"success": True, "code": "200", "message": "queued"}

    client = FakeClient()
    auth = SimpleNamespace(is_logged_in=True, username="student")
    service = CourseSelectionAutomationService(
        tmp_path, auth_provider=lambda: auth, client_builder=lambda _auth: client,
    )
    service.merge_catalog_archive(
        "student",
        batch={"code": "batch", "name": "权重轮次", "term_code": "2026-2027-1", "selection_type_code": "04"},
        scope="TJKC",
        groups=[{"group_id": "catalog", "classes": [
            {"class_id": "class-a", "course_code": "A", "course_name": "课程A", "capacity": 30, "weight_participant_count": 30},
            {"class_id": "class-b", "course_code": "B", "course_name": "课程B", "capacity": 30, "weight_participant_count": 30},
            {"class_id": "other", "course_code": "O", "course_name": "背景课", "capacity": 80, "weight_participant_count": 50},
        ]}],
    )
    service._archives[0]["catalog_complete"] = True
    service._archives[0]["sync_status"] = "complete"
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1", "name": "实时投权",
        "task_type": "weight_strategy", "grade_size": 126, "rebalance_seconds": 30,
        "groups": [{"group_id": "g", "name": "选修", "target_count": 1}],
        "items": [
            {"plan_group_id": "g", "priority": 1, "utility": 8, "course_code": "A", "course_name": "课程A", "class_id": "class-a", "teaching_class_type": "ALLKC", "capacity": 30, "weight_participant_count": 30},
            {"plan_group_id": "g", "priority": 2, "utility": 10, "course_code": "B", "course_name": "课程B", "class_id": "class-b", "teaching_class_type": "ALLKC", "capacity": 30, "weight_participant_count": 30},
        ],
    })
    task.update({"status": "running", "desired_state": "running", "last_attempt_at": None})

    service._tick(task)

    assert task["weight_status"]["last_calculated_at"]
    assert {item["class_id"] for item in task["weight_status"]["recommendation"]} == {"class-a", "class-b"}
    recommendation = {
        item["class_id"]: item for item in task["weight_status"]["recommendation"]
    }
    assert recommendation["class-a"]["action"] == "drop"
    assert recommendation["class-a"]["classification"] == "OUT"
    assert recommendation["class-b"]["action"] == "add"
    assert task["weight_status"]["pending_drop"][0]["class_id"] == "class-a"
    assert task["weight_status"]["pending_add"]
    assert task["execution"]["last_duration_ms"] is not None
    assert any(event["stage_code"] == "optimization" for event in task["execution"]["events"])
    assert client.dropped == []

    service._tick(task)
    assert client.dropped == ["class-a"]
    assert task["weight_status"]["inflight"]["action"] == "drop"

    service._tick(task)
    assert task["course_states"]["class-a"]["devoted_weight"] is None
    assert task["course_states"]["class-a"]["selected"] is False
    assert task["weight_status"]["inflight"]["action"] == "add"


def test_weight_strategy_refreshes_official_weight_before_waiting_for_catalog(tmp_path):
    class FakeClient:
        def get_selected(self, **_kwargs):
            return {
                "selected": [],
                "volunteered": [{
                    "class_id": "class-a", "course_code": "A",
                    "course_name": "课程A", "devoted_weight": 42,
                }],
            }

    auth = SimpleNamespace(is_logged_in=True, username="student")
    service = CourseSelectionAutomationService(
        tmp_path, auth_provider=lambda: auth, client_builder=lambda _auth: FakeClient(),
    )
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1", "name": "实时投权",
        "task_type": "weight_strategy", "grade_size": 100, "rebalance_seconds": 30,
        "groups": [{"group_id": "g", "name": "选修", "target_count": 1}],
        "items": [{
            "plan_group_id": "g", "course_code": "A", "course_name": "课程A",
            "class_id": "class-a", "teaching_class_type": "TJKC",
        }],
    })
    task.update({"status": "running", "desired_state": "running", "last_attempt_at": None})

    service._tick(task)

    assert task["attempt_count"] == 1
    assert task["course_states"]["class-a"]["devoted_weight"] == 42
    assert task["status"] == "waiting"
    assert task["message"] == "等待完整轮次课程数据同步后计算投权策略"


def test_group_quota_skips_full_candidates_and_keeps_filling_other_groups(tmp_path):
    class FakeClient:
        def __init__(self):
            self.selected = []
            self.submitted = []
            self.candidates = {
                "A1": {"class_id": "a1", "full": True},
                "A2": {"class_id": "a2", "full": False},
                "A3": {"class_id": "a3", "full": False},
                "B1": {"class_id": "b1", "full": False},
            }

        def get_selected(self, *, batch_code):
            return {"selected": list(self.selected), "volunteered": []}

        def search_courses(self, *, keyword, **_kwargs):
            return {"courses": [self.candidates[keyword]]}

        def check_course_eligibility(self, *, class_ids, **_kwargs):
            return {"results": [
                {"class_id": class_id, "status": "selectable", "reason": ""}
                for class_id in class_ids
            ]}

        def select_course(self, *, class_id, course_code, **_kwargs):
            self.submitted.append((class_id, course_code))
            return {"success": True, "code": "queued", "message": "已提交至官方队列"}

    client = FakeClient()
    auth = SimpleNamespace(is_logged_in=True, username="student")
    service = CourseSelectionAutomationService(
        tmp_path,
        auth_provider=lambda: auth,
        client_builder=lambda _auth: client,
    )
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1", "name": "分类目标",
        "groups": [
            {"group_id": "a", "name": "A 类", "target_count": 2},
            {"group_id": "b", "name": "B 类", "target_count": 1},
        ],
        "items": [
            {"plan_group_id": "a", "priority": 1, "course_code": "A1", "course_name": "A1", "class_id": "a1"},
            {"plan_group_id": "a", "priority": 2, "course_code": "A2", "course_name": "A2", "class_id": "a2"},
            {"plan_group_id": "a", "priority": 3, "course_code": "A3", "course_name": "A3", "class_id": "a3"},
            {"plan_group_id": "b", "priority": 1, "course_code": "B1", "course_name": "B1", "class_id": "b1"},
        ],
        "poll_seconds": 5,
    })
    task["status"] = "running"
    task["desired_state"] = "running"

    service._tick(task)
    assert client.submitted == [("a2", "A2"), ("b1", "B1")]
    assert task["group_results"]["a"]["status"] == "verifying"
    assert task["group_results"]["b"]["status"] == "verifying"

    client.selected = [
        {"class_id": "a2", "course_code": "A2"},
        {"class_id": "b1", "course_code": "B1"},
    ]
    task["last_attempt_at"] = None
    service._tick(task)
    assert client.submitted[-1] == ("a3", "A3")
    assert task["group_results"]["a"]["success_count"] == 1
    assert task["group_results"]["b"]["status"] == "success"

    client.selected.append({"class_id": "a3", "course_code": "A3"})
    task["last_attempt_at"] = None
    service._tick(task)
    assert task["group_results"]["a"]["success_count"] == 2
    assert task["group_results"]["a"]["status"] == "success"
    assert task["status"] == "success"


def test_vacancy_swap_waits_for_drop_confirmation_before_selecting_target(tmp_path):
    class FakeClient:
        def __init__(self):
            self.selected = [{"class_id": "drop-1", "course_code": "DROP"}]
            self.full = True
            self.dropped = []
            self.submitted = []

        def get_selected(self, **_kwargs):
            return {"selected": list(self.selected), "volunteered": []}

        def search_courses(self, **_kwargs):
            return {"courses": [{
                "class_id": "target-1", "course_code": "TARGET", "full": self.full,
            }]}

        def deselect_course(self, *, class_id, **_kwargs):
            self.dropped.append(class_id)
            return {"success": True, "code": "queued", "message": "退选已提交"}

        def check_course_eligibility(self, *, class_ids, **_kwargs):
            return {"results": [{"class_id": class_ids[0], "status": "selectable", "reason": ""}]}

        def select_course(self, *, class_id, **_kwargs):
            self.submitted.append(class_id)
            return {"success": True, "code": "queued", "message": "选课已提交"}

    client = FakeClient()
    auth = SimpleNamespace(is_logged_in=True, username="student")
    service = CourseSelectionAutomationService(
        tmp_path, auth_provider=lambda: auth, client_builder=lambda _auth: client,
    )
    task = service.create("student", {
        "batch_code": "batch", "term_code": "2026-2027-1", "name": "换课",
        "task_type": "vacancy_swap", "groups": [], "items": [], "poll_seconds": 5,
        "swap_groups": [{
            "group_id": "swap-1", "name": "目标课程",
            "target": {"class_id": "target-1", "course_code": "TARGET", "course_name": "目标课程", "teaching_class_type": "FANKC"},
            "drop_courses": [{"class_id": "drop-1", "course_code": "DROP", "course_name": "腾位课程", "teaching_class_type": "FANKC"}],
        }],
    })
    task["status"] = "running"
    task["desired_state"] = "running"

    service._tick(task)
    assert client.dropped == []
    assert task["swap_results"]["swap-1"]["status"] == "monitoring"

    client.full = False
    task["last_attempt_at"] = None
    service._tick(task)
    assert client.dropped == ["drop-1"]
    assert client.submitted == []
    assert task["swap_results"]["swap-1"]["status"] == "verifying_drop"

    client.selected = []
    task["last_attempt_at"] = None
    service._tick(task)
    assert client.submitted == ["target-1"]
    assert task["swap_results"]["swap-1"]["status"] == "verifying"

    client.selected = [{"class_id": "target-1", "course_code": "TARGET"}]
    task["last_attempt_at"] = None
    service._tick(task)
    assert task["swap_results"]["swap-1"]["status"] == "success"
    assert task["status"] == "success"
