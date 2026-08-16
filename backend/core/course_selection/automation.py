"""Persisted, explicitly-started JWXK auto-selection tasks."""

from __future__ import annotations

import json
import copy
import logging
import threading
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from backend.core.auth.client import NEULoginError
from backend.core.runtime.config import secure_file
from backend.core.scheduling import check_conflicts, normalize_meeting

from .jwxk import (
    JwxkError, JwxkRateLimitError, JwxkSessionClient, course_categories_equivalent,
    group_course_rows, normalize_jwxk_campus_code,
)
from .weight_optimizer import (
    WeightCandidate,
    WeightGroupTarget,
    WeightMarketCourse,
    WeightOptimizationError,
    WeightPolicy,
    optimize_grouped_weights,
)


_OFFICIAL_TIMEZONE = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)


class CourseSelectionAutomationService:
    """Own automatic tasks and user-kept catalog archives outside the cache."""

    # Refresh every real menu scope in the active round, excluding ALLKC.
    # Candidate classes are still refreshed on every strategy check; this
    # slower full-market cadence limits request volume and school-side risk.
    _CATALOG_DYNAMIC_REFRESH_SECONDS = 600
    _CATALOG_RETRY_MAX_SECONDS = 300
    _CATALOG_STALE_PROGRESS_SECONDS = 120
    _CATALOG_DYNAMIC_FIELDS = (
        "capacity", "selected_count", "full",
        "first_choice_count", "weight_participant_count", "market_participant_count",
    )

    def __init__(
        self,
        data_dir: str | Path,
        *,
        auth_provider: Callable[[], Any],
        auth_recover_provider: Callable[[], Any] | None = None,
        client_builder: Callable[[Any], JwxkSessionClient],
        remote_guard: Callable[[], Any] | None = None,
        notification_provider: Callable[[str, str, str], bool] | None = None,
    ) -> None:
        self.path = Path(data_dir) / "course_selection_tasks.json"
        self.archive_path = Path(data_dir) / "course_selection_catalog_history.json"
        self.settings_path = Path(data_dir) / "course_selection_automation_settings.json"
        self.notification_state_path = Path(data_dir) / "course_selection_notification_state.json"
        self.auth_provider = auth_provider
        self.auth_recover_provider = auth_recover_provider or auth_provider
        self.client_builder = client_builder
        self.remote_guard = remote_guard or nullcontext
        self.notification_provider = notification_provider
        self._lock = threading.RLock()
        self._wake = threading.Event()
        # Catalog archiving is deliberately isolated from the task scheduler.
        # A complete round may contain thousands of classes; it must not block
        # the one-second/30-second automation loop or make a live task appear
        # to be stuck in authentication recovery.
        self._catalog_sync_wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._catalog_thread: threading.Thread | None = None
        self._tasks = [task for task in self._read() if task.get("status") != "cancelled"]
        self._settings = self._read_json_map(self.settings_path)
        self._notification_state = self._read_json_map(self.notification_state_path)
        self._archives = self._read_archives()
        archives_changed = self._remove_directory_only_courses()
        for archive in self._archives:
            if not archive.get("archived") and archive.get("sync_status") in {"queued", "running"}:
                archive["sync_status"] = "pending"
                archive["sync_error"] = "程序重启后等待重新同步"
                archives_changed = True
        self._last_archive_scan = 0.0
        self._catalog_sync_queue: set[tuple[str, str]] = set()
        for task in self._tasks:
            self._normalize_group_results(task)
            self._normalize_swap_results(task)
            task.setdefault("weight_status", {})
            task.setdefault("execution", {})
            task.setdefault(
                "desired_state",
                "running" if task.get("status") in {"running", "waiting"} else "paused",
            )
            task.setdefault("polling_mode", self._initial_polling_mode(task))
            if task.get("desired_state") == "running" and task.get("status") in {"running", "waiting"}:
                task["status"] = "waiting"
                task["restart_reconcile"] = True
                task["message"] = "程序已重新启动，正在核验官方状态后继续任务"
        self._write()
        if archives_changed:
            self._write_archives()

    def _read(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, ValueError, TypeError):
            return []

    @staticmethod
    def _read_json_map(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _write_json_map(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        secure_file(path)

    @staticmethod
    def default_automation_settings() -> dict[str, Any]:
        return {
            "strategy_schedule_mode": "interval",
            "rebalance_seconds": 600,
            "force_final_rebalance": True,
            "mail_enabled": False,
            "notify_round_end": False,
            "notify_final_rebalance": False,
            "notify_capacity_transition": False,
            "notify_over_capacity": False,
            "notify_underfilled_warning": False,
            "notify_grab_result": False,
            "over_capacity_ratio": 0.20,
        }

    def get_automation_settings(self, account: str, batch_code: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        key = f"{account}:{batch_code}"
        with self._lock:
            allowed = set(self.default_automation_settings())
            stored = {k: v for k, v in (self._settings.get(key) or {}).items() if k in allowed}
            value = {**self.default_automation_settings(), **stored}
            value.update({"batch_code": batch_code, **(metadata or {})})
            return copy.deepcopy(value)

    def update_automation_settings(self, account: str, batch_code: str, values: dict[str, Any], *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        key = f"{account}:{batch_code}"
        allowed = set(self.default_automation_settings())
        candidate = {k: copy.deepcopy(v) for k, v in values.items() if k in allowed}
        with self._lock:
            previous = {k: v for k, v in (self._settings.get(key) or {}).items() if k in allowed}
            merged = {**self.default_automation_settings(), **previous, **candidate}
            self._settings[key] = merged
            self._write_json_map(self.settings_path, self._settings)
            result = {**merged, "batch_code": batch_code, **(metadata or {})}
            return copy.deepcopy(result)

    def _queue_notification(self, account: str, batch_code: str, subject: str, body: str, key: str) -> bool:
        if not self.notification_provider:
            return False
        try:
            return bool(self.notification_provider(subject, body, f"{account}:{batch_code}:{key}"))
        except Exception:
            logger.exception("course selection notification enqueue failed")
            return False

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self._tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        secure_file(self.path)

    def _read_archives(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self.archive_path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, ValueError, TypeError):
            return []

    def _write_archives(self) -> None:
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.archive_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self._archives, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.archive_path)
        secure_file(self.archive_path)

    @staticmethod
    def _persistable_archive_course(course: dict[str, Any]) -> dict[str, Any] | None:
        """Drop ALLKC-only discovery rows from durable round data.

        ALLKC is the school's global search directory.  It is useful for a
        live explicit search, but it is not a round-owned course source and is
        mostly duplicate data.  A class is durable only when another official
        round scope (for example TJKC/FANKC/XGKC) also contains it.
        """
        value = copy.deepcopy(course)
        scopes = [
            str(item or "") for item in value.get("source_scopes") or [
                value.get("teaching_class_type")
            ]
            if str(item or "")
        ]
        real_scopes = [
            item for item in scopes
            if item not in {"ALL", "ROUND", "ALLKC"}
        ]
        if not real_scopes:
            return None
        value["source_scopes"] = [item for item in scopes if item != "ALLKC"]
        value["source_tags"] = [
            item for item in value.get("source_tags") or []
            if str(item or "") not in {"ALLKC", "全校课程查询"}
        ]
        if str(value.get("teaching_class_type") or "") in {"", "ALL", "ROUND", "ALLKC"}:
            value["teaching_class_type"] = real_scopes[0]
        return value

    def _remove_directory_only_courses(self) -> bool:
        changed = False
        for archive in self._archives:
            previous = list(archive.get("courses") or [])
            cleaned = [
                normalized for course in previous
                if (normalized := self._persistable_archive_course(course)) is not None
            ]
            if cleaned != previous:
                archive["courses"] = cleaned
                archive["sync_scopes"] = [
                    scope for scope in archive.get("sync_scopes") or []
                    if str(scope or "") != "ALLKC"
                ]
                archive["sync_loaded"] = min(
                    int(archive.get("sync_loaded") or 0), len(cleaned),
                )
                archive["sync_total"] = min(
                    int(archive.get("sync_total") or 0), len(cleaned),
                )
                archive["updated_at"] = datetime.now().astimezone().isoformat()
                changed = True
        return changed

    @staticmethod
    def _archive_class(group: dict[str, Any], course: dict[str, Any], scope: str) -> dict[str, Any]:
        allowed = {
            "course_code", "course_name", "class_id", "class_number", "credits", "hours",
            "teacher", "location", "official_schedule", "campus", "campus_name", "department",
            "course_nature", "course_category", "course_categories",
            "normalized_course_category", "general_elective_category_code",
            "general_elective_category", "exam_type_code", "exam_type",
            "score_scale_code", "score_scale", "teaching_mode", "teacher_details",
            "teacher_titles", "target_classes", "capacity",
            "selected_count", "first_choice_count", "weight_participant_count", "devoted_weight",
            "selection_type_code", "market_participant_count", "market_participant_label",
            "conflict", "conflict_description", "restricted", "eligibility_status",
            "eligibility_reason", "full", "selected", "has_test", "has_book", "notice", "schedules",
        }
        value = {key: copy.deepcopy(course.get(key)) for key in allowed if key in course}
        effective_scope = str(course.get("teaching_class_type") or scope)
        value.update({
            "group_id": str(group.get("group_id") or ""),
            "source_tags": list(group.get("source_tags") or []),
            "teaching_class_type": effective_scope,
            "source_scopes": list(course.get("source_scopes") or [effective_scope]),
        })
        return value

    def merge_catalog_archive(
        self,
        account: str,
        *,
        batch: dict[str, Any],
        scope: str,
        groups: list[dict[str, Any]],
    ) -> dict[str, Any]:
        batch_code = str(batch.get("code") or "")
        if not account or not batch_code:
            raise ValueError("account and batch code are required")
        now = datetime.now().astimezone().isoformat()
        batch_scope_options = []
        seen_scope_codes: set[str] = set()
        for item in batch.get("menus") or []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or item.get("teachingClassType") or "").strip()
            if not code or code in {"ALL", "ROUND"} or code in seen_scope_codes:
                continue
            seen_scope_codes.add(code)
            name = str(item.get("name") or item.get("displayName") or code).strip() or code
            batch_scope_options.append({"code": code, "name": name})
        incoming = [
            self._archive_class(group, course, scope)
            for group in groups if isinstance(group, dict)
            for course in (group.get("classes") or []) if isinstance(course, dict)
            and str(course.get("class_id") or "")
        ] if scope != "ALLKC" else []
        with self._lock:
            archive = next((item for item in self._archives if item.get("account") == account and item.get("batch_code") == batch_code), None)
            if archive is None:
                archive = {
                    "archive_id": uuid.uuid4().hex,
                    "account": account,
                    "batch_code": batch_code,
                    "batch_name": str(batch.get("name") or "选课轮次"),
                    "term_code": str(batch.get("term_code") or ""),
                    "term_name": str(batch.get("term_name") or ""),
                    "selection_type_code": str(batch.get("selection_type_code") or ""),
                    "begin_time": str(batch.get("begin_time") or ""),
                    "end_time": str(batch.get("end_time") or ""),
                    "archived": False,
                    "created_at": now,
                    "updated_at": now,
                    "final_refresh_at": None,
                    "final_refresh_status": "pending",
                    "sync_status": "pending",
                    "catalog_complete": False,
                    "sync_scopes": [],
                    "sync_loaded": 0,
                    "sync_total": 0,
                    "last_sync_at": None,
                    "scope_options": [],
                    "courses": [],
                }
                self._archives.append(archive)
            for key in (
                "name", "term_code", "term_name", "selection_type_code", "begin_time", "end_time",
            ):
                target = "batch_name" if key == "name" else key
                if batch.get(key):
                    archive[target] = str(batch[key])
            if batch_scope_options:
                archive["scope_options"] = batch_scope_options
            persisted = [
                normalized for item in archive.get("courses") or []
                if (normalized := self._persistable_archive_course(item)) is not None
            ]
            by_class = {str(item.get("class_id") or ""): item for item in persisted}
            for course in incoming:
                class_id = str(course["class_id"])
                if any(course.get(key) is not None for key in self._CATALOG_DYNAMIC_FIELDS):
                    course["capacity_updated_at"] = now
                previous = by_class.get(class_id)
                if previous:
                    source_tags = sorted(set(previous.get("source_tags") or []) | set(course.get("source_tags") or []))
                    source_scopes = sorted(set(previous.get("source_scopes") or [previous.get("teaching_class_type")]) | {
                        str(course.get("teaching_class_type") or "")
                    })
                    preferred_scope = next((
                        value for value in source_scopes
                        if value and value not in {"ALL", "ROUND", "ALLKC"}
                    ), None)
                    course = {
                        **previous,
                        **course,
                        "source_tags": source_tags,
                        "source_scopes": [value for value in source_scopes if value],
                        "teaching_class_type": preferred_scope or str(course.get("teaching_class_type") or previous.get("teaching_class_type") or "ALLKC"),
                    }
                    if (
                        str(course.get("eligibility_status") or "unknown") == "unknown"
                        and str(previous.get("eligibility_status") or "unknown") != "unknown"
                    ):
                        course["eligibility_status"] = previous.get("eligibility_status")
                        course["eligibility_reason"] = previous.get("eligibility_reason") or ""
                else:
                    course["source_scopes"] = [str(course.get("teaching_class_type") or scope)]
                by_class[class_id] = course
            archive["courses"] = list(by_class.values())
            archive["updated_at"] = now
            self._write_archives()
            self._wake.set()
            self._catalog_sync_wake.set()
            return copy.deepcopy(archive)

    def schedule_catalog_sync(
        self, account: str, *, batch: dict[str, Any], force: bool = False,
    ) -> None:
        """Queue a non-blocking complete scan of the round's real catalog scopes."""
        batch_code = str(batch.get("code") or "")
        if not account or not batch_code:
            return
        self.merge_catalog_archive(account, batch=batch, scope="ROUND", groups=[])
        with self._lock:
            archive = next((item for item in self._archives if (
                item.get("account") == account and item.get("batch_code") == batch_code
            )), None)
            if archive is None or archive.get("archived"):
                return
            if archive.get("sync_status") in {"queued", "running"}:
                return
            if archive.get("sync_status") == "complete" and not force:
                last_sync_at = archive.get("last_sync_at")
                if last_sync_at:
                    try:
                        elapsed = datetime.now().astimezone() - datetime.fromisoformat(
                            str(last_sync_at)
                        ).astimezone()
                        if elapsed.total_seconds() < self._CATALOG_DYNAMIC_REFRESH_SECONDS:
                            return
                    except ValueError:
                        pass
            archive["sync_status"] = "queued"
            archive["sync_error"] = ""
            archive["sync_failure_count"] = 0
            archive["sync_retry_at"] = ""
            archive["sync_loaded"] = 0
            archive["sync_total"] = 0
            archive["sync_current_scope"] = ""
            archive["sync_current_page"] = 0
            archive["updated_at"] = datetime.now().astimezone().isoformat()
            self._catalog_sync_queue.add((account, batch_code))
            self._write_archives()
            self._catalog_sync_wake.set()

    def update_archive_eligibility(
        self, account: str, *, batch_code: str, results: list[dict[str, Any]]
    ) -> None:
        result_map = {str(item.get("class_id") or ""): item for item in results}
        if not result_map:
            return
        with self._lock:
            archive = next((item for item in self._archives if item.get("account") == account and item.get("batch_code") == batch_code), None)
            if archive is None:
                return
            for course in archive.get("courses") or []:
                result = result_map.get(str(course.get("class_id") or ""))
                if result:
                    course["eligibility_status"] = result.get("status") or "unknown"
                    course["eligibility_reason"] = result.get("reason") or ""
            archive["updated_at"] = datetime.now().astimezone().isoformat()
            self._write_archives()

    def list_catalog_archives(self, account: str) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(item) for item in self._archives if item.get("account") == account]

    def get_catalog_archive_view(self, account: str, batch_code: str) -> dict[str, Any] | None:
        """Return a read-only shallow view without copying the complete catalog."""
        with self._lock:
            source = next((item for item in self._archives if (
                item.get("account") == account and item.get("batch_code") == batch_code
            )), None)
            if source is None:
                return None
            return {
                **{key: value for key, value in source.items() if key != "courses"},
                "courses": tuple(source.get("courses") or ()),
            }

    def query_catalog_archive(
        self,
        account: str,
        *,
        batch_code: str,
        page_number: int,
        page_size: int,
        scope: str = "ALL",
        keyword: str = "",
        campus: str = "",
        filters: dict[str, str] | None = None,
        time_slot: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        """Query the progressively-built, account-scoped union catalog."""
        with self._lock:
            source = next((item for item in self._archives if (
                item.get("account") == account and item.get("batch_code") == batch_code
            )), None)
            archive = None if source is None else {
                "courses": tuple(source.get("courses") or ()),
                "sync_status": source.get("sync_status"),
                "catalog_complete": source.get("catalog_complete"),
            }
        if archive is None or not archive.get("courses"):
            return None

        query = keyword.strip().casefold()
        remote_filters = filters or {}

        def matches(course: dict[str, Any]) -> bool:
            provenance_scopes = {
                str(value or "") for value in course.get("source_scopes") or []
            } or {str(course.get("teaching_class_type") or "")}
            course_scopes = {
                str(course.get("teaching_class_type") or ""),
                *provenance_scopes,
            }
            if scope in {"", "ALL", "ROUND"}:
                if not any(value not in {"", "ALL", "ROUND", "ALLKC"} for value in provenance_scopes):
                    return False
            elif scope not in course_scopes:
                return False
            if query and query not in " ".join((
                str(course.get("course_code") or ""),
                str(course.get("course_name") or ""),
                str(course.get("teacher") or ""),
            )).casefold():
                return False
            if campus:
                requested_campus = normalize_jwxk_campus_code(campus)
                course_campuses = {
                    normalize_jwxk_campus_code(course.get("campus")),
                    normalize_jwxk_campus_code(course.get("campus_name")),
                    *(normalize_jwxk_campus_code(item.get("campus")) for item in course.get("schedules") or []),
                    *(normalize_jwxk_campus_code(item.get("campus_name")) for item in course.get("schedules") or []),
                }
                if requested_campus not in course_campuses:
                    return False
            field_filters = {
                "KCXZ": "course_nature", "KCLB": "course_category",
                "XGXKLB": "general_elective_category", "KKDW": "department",
            }
            for remote_key, local_key in field_filters.items():
                value = str(remote_filters.get(remote_key) or "")
                if not value:
                    continue
                if remote_key == "KCLB":
                    categories = [
                        str(course.get("course_category") or ""),
                        *(str(item) for item in course.get("course_categories") or []),
                    ]
                    if not any(course_categories_equivalent(item, value) for item in categories):
                        return False
                elif str(course.get(local_key) or "") != value:
                    return False
            if remote_filters.get("SFCT") == "0" and course.get("conflict"):
                return False
            if remote_filters.get("SFYM") == "0" and course.get("full"):
                return False
            if remote_filters.get("SFYX") == "1" and not course.get("selected"):
                return False
            meetings = course.get("schedules") or []
            weekday = int(remote_filters.get("SKXQ") or 0)
            start_section = int(remote_filters.get("KSJC") or 0)
            end_section = int(remote_filters.get("JSJC") or 0)
            if weekday and not any(int(item.get("weekday") or 0) == weekday for item in meetings):
                return False
            if start_section and not any(int(item.get("start_section") or 0) == start_section for item in meetings):
                return False
            if end_section and not any(int(item.get("end_section") or 0) == end_section for item in meetings):
                return False
            if time_slot and not any(
                int(item.get("weekday") or 0) == int(time_slot["weekday"])
                and int(item.get("start_section") or 0) <= int(time_slot["section"])
                <= int(item.get("end_section") or 0)
                for item in meetings
            ):
                return False
            return True

        courses = [course for course in archive.get("courses") or [] if matches(course)]
        if scope in {"", "ALL", "ROUND"}:
            courses = [{
                **course,
                "source_scopes": [
                    value for value in course.get("source_scopes") or []
                    if str(value or "") != "ALLKC"
                ],
                "source_tags": [
                    value for value in course.get("source_tags") or []
                    if str(value or "") not in {"ALLKC", "全校课程查询"}
                ],
            } for course in courses]
        if scope not in {"", "ALL", "ROUND"}:
            courses = [
                {
                    **course,
                    "teaching_class_type": scope
                    if scope in set(course.get("source_scopes") or []) else course.get("teaching_class_type"),
                }
                for course in courses
            ]
        tags_by_code: dict[str, set[str]] = {}
        for course in courses:
            code = str(course.get("course_code") or "")
            if code:
                tags_by_code.setdefault(code, set()).update(course.get("source_tags") or [])
        groups = group_course_rows(courses, source_tags=tags_by_code)
        def class_rank(course: dict[str, Any]) -> int:
            if course.get("full") or course.get("restricted") or course.get("eligibility_status") == "unavailable":
                return 3
            if course.get("eligibility_status") == "unknown":
                return 2
            if course.get("conflict"):
                return 1
            return 0

        groups.sort(key=lambda group: (
            min((class_rank(course) for course in group.get("classes") or []), default=4),
            0 if any(
                any(scope not in {"ALL", "ALLKC"} for scope in course.get("source_scopes") or [])
                for course in group.get("classes") or []
            ) else 1,
            str(group.get("course_name") or ""),
            str(group.get("course_code") or ""),
        ))
        start = (page_number - 1) * page_size
        return {
            "total": len(groups),
            "groups": groups[start:start + page_size],
            "sync_status": str(archive.get("sync_status") or "pending"),
            "catalog_complete": bool(
                archive.get("catalog_complete") or archive.get("sync_status") == "complete"
            ),
        }

    def delete_catalog_archive(self, account: str, archive_id: str) -> bool:
        with self._lock:
            before = len(self._archives)
            self._archives = [item for item in self._archives if not (
                item.get("account") == account and item.get("archive_id") == archive_id
            )]
            if len(self._archives) == before:
                return False
            self._write_archives()
            return True

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="jwxk-auto-selection", daemon=True)
            self._thread.start()
            self._catalog_thread = threading.Thread(
                target=self._run_catalog_background,
                name="jwxk-catalog-background",
                daemon=True,
            )
            self._catalog_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._catalog_sync_wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        if self._catalog_thread and self._catalog_thread.is_alive():
            self._catalog_thread.join(timeout=3)

    def list(self, account: str, batch_code: str = "") -> list[dict[str, Any]]:
        with self._lock:
            return [
                self._task_snapshot(task) for task in self._tasks
                if task.get("account") == account
                and (not batch_code or task.get("batch_code") == batch_code)
            ]

    def _task_snapshot(self, task: dict[str, Any]) -> dict[str, Any]:
        snapshot = copy.deepcopy(task)
        public_course_fields = {
            "plan_group_id", "plan_group_name", "plan_group_target_count",
            "course_code", "course_name", "class_id", "class_number",
            "teaching_class_type", "teacher", "priority", "utility",
            "capacity", "selected_count", "first_choice_count",
            "weight_participant_count", "market_participant_count",
            "market_participant_label", "devoted_weight", "weight",
        }
        snapshot["items"] = [
            {key: value for key, value in item.items() if key in public_course_fields}
            for item in snapshot.get("items") or [] if isinstance(item, dict)
        ]
        weight_status = snapshot.get("weight_status")
        if isinstance(weight_status, dict):
            weight_status["recommendation"] = [
                {key: value for key, value in item.items() if key in public_course_fields}
                for item in weight_status.get("recommendation") or [] if isinstance(item, dict)
            ]
        snapshot["results"] = list(snapshot.get("results") or [])[-20:]
        interval = self._poll_interval(task)
        snapshot["poll_interval_seconds"] = interval
        snapshot["next_attempt_at"] = None
        if task.get("desired_state") == "running" and task.get("status") in {"running", "waiting"}:
            if task.get("manual_check_requested_at"):
                snapshot["next_attempt_at"] = datetime.now().astimezone().isoformat()
            else:
                last_attempt = self._parse_task_time(task.get("last_attempt_at"))
                if last_attempt is not None:
                    snapshot["next_attempt_at"] = (last_attempt + timedelta(seconds=interval)).isoformat()
        return snapshot

    @staticmethod
    def _group_specs(task: dict[str, Any]) -> list[dict[str, Any]]:
        declared = {
            str(group.get("group_id") or ""): {
                "group_id": str(group.get("group_id") or ""),
                "name": str(group.get("name") or "方案组"),
                "target_count": max(1, int(group.get("target_count") or 1)),
            }
            for group in task.get("groups") or []
            if str(group.get("group_id") or "")
        }
        for item in task.get("items") or []:
            group_id = str(item.get("plan_group_id") or item.get("course_code") or item.get("class_id") or "")
            if group_id and group_id not in declared:
                declared[group_id] = {
                    "group_id": group_id,
                    "name": str(item.get("plan_group_name") or item.get("course_name") or "方案组"),
                    "target_count": max(1, int(item.get("plan_group_target_count") or 1)),
                }
        return list(declared.values())

    @staticmethod
    def _empty_group_result(spec: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "pending",
            "message": "等待监测",
            "target_count": int(spec["target_count"]),
            "success_count": 0,
            "selected": [],
            "pending_class_ids": [],
        }

    def _normalize_group_results(self, task: dict[str, Any]) -> None:
        previous = task.get("group_results") if isinstance(task.get("group_results"), dict) else {}
        normalized: dict[str, dict[str, Any]] = {}
        for spec in self._group_specs(task):
            group_id = spec["group_id"]
            value = dict(previous.get(group_id) or {})
            selected = value.get("selected") if isinstance(value.get("selected"), list) else []
            if not selected and value.get("class_id"):
                selected = [{
                    "class_id": value.get("class_id"),
                    "course_code": value.get("course_code") or "",
                    "course_name": value.get("course_name") or "",
                }]
            value.update({
                "target_count": int(spec["target_count"]),
                "selected": selected,
                "success_count": len({
                    str(item.get("course_code") or item.get("class_id") or "")
                    for item in selected
                    if str(item.get("course_code") or item.get("class_id") or "")
                }),
                "pending_class_ids": list(value.get("pending_class_ids") or []),
            })
            if value["success_count"] >= value["target_count"]:
                value["status"] = "success"
            else:
                value["status"] = "verifying" if value["pending_class_ids"] else "pending"
                value.setdefault("message", "等待监测")
            normalized[group_id] = value
        task["group_results"] = normalized

    @staticmethod
    def _normalize_swap_results(task: dict[str, Any]) -> None:
        previous = task.get("swap_results") if isinstance(task.get("swap_results"), dict) else {}
        task["swap_results"] = {
            str(group.get("group_id") or ""): {
                "status": str((previous.get(str(group.get("group_id") or "")) or {}).get("status") or "monitoring"),
                "message": str((previous.get(str(group.get("group_id") or "")) or {}).get("message") or "等待开始追踪空位"),
                "pending_drop_ids": list((previous.get(str(group.get("group_id") or "")) or {}).get("pending_drop_ids") or []),
                "pending_checks": int((previous.get(str(group.get("group_id") or "")) or {}).get("pending_checks") or 0),
                "target_pending": bool((previous.get(str(group.get("group_id") or "")) or {}).get("target_pending")),
            }
            for group in task.get("swap_groups") or []
            if str(group.get("group_id") or "")
        }

    def create(self, account: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now().astimezone().isoformat()
        task = {
            **payload,
            "task_id": uuid.uuid4().hex,
            "account": account,
            "status": "draft",
            "desired_state": "paused",
            "message": "任务已保存，启动后才会访问选课系统",
            "created_at": now,
            "updated_at": now,
            "last_attempt_at": None,
            "attempt_count": 0,
            "results": [],
        }
        task["polling_mode"] = self._initial_polling_mode(task)
        task["group_results"] = {
            spec["group_id"]: self._empty_group_result(spec)
            for spec in self._group_specs(task)
        }
        self._normalize_swap_results(task)
        if task.get("task_type") == "weight_strategy":
            task["weight_status"] = {
                "phase": "idle",
                "last_calculated_at": None,
                "last_adjusted_at": None,
                "recommendation": [],
                "pending_drop": [],
                "pending_add": [],
                "inflight": None,
                "confirmation_checks": 0,
            }
        task["execution"] = {
            "state": "idle",
            "stage": "尚未执行",
            "stage_code": "idle",
            "trigger": "",
            "check_started_at": None,
            "stage_started_at": None,
            "last_completed_at": None,
            "last_duration_ms": None,
            "last_error_stage": "",
            "last_error_type": "",
            "last_error_message": "",
            "last_error_at": None,
            "events": [],
        }
        with self._lock:
            self._tasks.append(task)
            self._write()
        return dict(task)

    def action(self, account: str, task_id: str, action: str) -> dict[str, Any]:
        with self._lock:
            task = next((item for item in self._tasks if item.get("task_id") == task_id and item.get("account") == account), None)
            if task is None:
                raise KeyError(task_id)
            if action == "start":
                task["desired_state"] = "running"
                task["status"] = "waiting" if self._before_start(task) else "running"
                task["restart_reconcile"] = True
                if task.get("task_type") == "vacancy_swap":
                    task["message"] = f"正在同时追踪 {len(task.get('swap_results') or {})} 个空位换课组"
                elif task.get("task_type") == "weight_strategy":
                    task["message"] = "实时策略已启动，正在读取最新已投注人数"
                elif task["status"] == "waiting":
                    task["message"] = "等待轮次开放，开放后将立即开始抢选"
                else:
                    task["message"] = f"正在同时监测 {len(task.get('group_results') or {})} 个方案组"
            elif action == "pause":
                task["desired_state"] = "paused"
                task["status"] = "paused"
                task["message"] = "任务已暂停"
            elif action == "check_now":
                if task.get("task_type") != "weight_strategy":
                    raise ValueError("立即检查并执行策略仅适用于策略投权任务")
                if task.get("desired_state") != "running" or task.get("status") not in {"running", "waiting"}:
                    raise ValueError("请先启动任务，再执行立即检查")
                task["manual_check_requested_at"] = datetime.now().astimezone().isoformat()
                if (task.get("execution") or {}).get("state") == "running":
                    task["message"] = "当前检查仍在执行，完成后将立即再次检查并按策略行动"
                else:
                    task["message"] = "已请求立即检查，正在等待后台执行"
            elif action == "cancel":
                task["desired_state"] = "cancelled"
                task["status"] = "cancelled"
                self._tasks = [item for item in self._tasks if item is not task]
                self._write()
                return {
                    "task_id": task_id,
                    "status": "cancelled",
                    "message": "任务已取消并删除",
                }
            else:
                raise ValueError(action)
            task["updated_at"] = datetime.now().astimezone().isoformat()
            self._write()
            self._wake.set()
            return dict(task)

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                due = [
                    task for task in self._tasks
                    if task.get("desired_state") == "running"
                    and task.get("status") in {"running", "waiting"}
                ]
            for task in due:
                if self._stop.is_set():
                    break
                self._tick(task)
            # Keep this loop exclusively for user-started tasks.  Catalog
            # archiving runs in _run_catalog_background so a large scan cannot
            # delay a live strategy or vacancy task.
            self._wake.wait(timeout=1)
            self._wake.clear()

    def _run_catalog_background(self) -> None:
        """Process rebuildable catalog work without blocking task scheduling."""
        while not self._stop.is_set():
            self._reset_stale_catalog_syncs()
            self._requeue_failed_catalog_syncs()
            self._tick_catalog_sync()
            if time.time() - self._last_archive_scan >= 60:
                self._last_archive_scan = time.time()
                self._tick_catalog_archives()
            self._catalog_sync_wake.wait(timeout=1)
            self._catalog_sync_wake.clear()

    def _reset_stale_catalog_syncs(self) -> None:
        """Turn a read-only catalog scan with no progress back into retry state."""
        now = datetime.now().astimezone()
        changed = False
        with self._lock:
            for archive in self._archives:
                if archive.get("archived") or archive.get("sync_status") != "running":
                    continue
                try:
                    updated = datetime.fromisoformat(str(archive.get("updated_at") or "")).astimezone()
                except ValueError:
                    updated = now - timedelta(seconds=self._CATALOG_STALE_PROGRESS_SECONDS + 1)
                if (now - updated).total_seconds() <= self._CATALOG_STALE_PROGRESS_SECONDS:
                    continue
                failures = int(archive.get("sync_failure_count") or 0) + 1
                delay = min(self._CATALOG_RETRY_MAX_SECONDS, 15 * (2 ** min(failures - 1, 5)))
                archive.update({
                    "sync_status": "failed",
                    "sync_error": "学校数据读取长时间没有进展，已自动重置",
                    "sync_failure_count": failures,
                    "sync_retry_at": (now + timedelta(seconds=delay)).isoformat(),
                    "updated_at": now.isoformat(),
                })
                changed = True
            if changed:
                self._write_archives()

    def _requeue_failed_catalog_syncs(self) -> None:
        """Retry transient read-only catalog failures with bounded backoff."""
        auth = self.auth_recover_provider()
        account = str(getattr(auth, "username", "") or "") if auth is not None else ""
        if not account:
            return
        now = datetime.now().astimezone()
        changed = False
        with self._lock:
            for archive in self._archives:
                if (
                    archive.get("archived")
                    or archive.get("account") != account
                    or archive.get("sync_status") not in {"failed", "paused"}
                ):
                    continue
                try:
                    retry_at = datetime.fromisoformat(str(archive.get("sync_retry_at") or "")).astimezone()
                except ValueError:
                    retry_at = now
                if now < retry_at:
                    continue
                archive.update({
                    "sync_status": "queued",
                    "sync_loaded": 0,
                    "sync_total": 0,
                    "sync_current_scope": "",
                    "sync_current_page": 0,
                    "updated_at": now.isoformat(),
                })
                self._catalog_sync_queue.add((account, str(archive.get("batch_code") or "")))
                changed = True
            if changed:
                self._write_archives()
                self._catalog_sync_wake.set()

    @staticmethod
    def _parse_task_time(value: Any) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value or ""))
            return (
                parsed.astimezone(_OFFICIAL_TIMEZONE)
                if parsed.tzinfo
                else parsed.replace(tzinfo=_OFFICIAL_TIMEZONE)
            )
        except ValueError:
            return None

    @classmethod
    def _before_start(cls, task: dict[str, Any]) -> bool:
        start = cls._parse_task_time(task.get("start_at"))
        return bool(start and datetime.now(_OFFICIAL_TIMEZONE) < start)

    @classmethod
    def _initial_polling_mode(cls, task: dict[str, Any]) -> str:
        if task.get("task_type") == "weight_strategy":
            return "weight_rebalance"
        if task.get("task_type") == "vacancy_swap":
            return "vacancy_watch"
        start = cls._parse_task_time(task.get("start_at"))
        created = cls._parse_task_time(task.get("created_at")) or datetime.now(_OFFICIAL_TIMEZONE)
        return "opening_burst" if start and created < start else "vacancy_watch"

    @staticmethod
    def _poll_interval(task: dict[str, Any]) -> int:
        if task.get("polling_mode") == "weight_rebalance":
            return max(600, int(task.get("rebalance_seconds") or 600))
        if task.get("polling_mode") == "opening_burst":
            return 1
        return max(15, int(task.get("poll_seconds") or 15))

    @staticmethod
    def _consume_manual_check(task: dict[str, Any]) -> bool:
        return bool(task.pop("manual_check_requested_at", None))

    @staticmethod
    def _execution_elapsed_ms(started_at: Any) -> int | None:
        try:
            started = datetime.fromisoformat(str(started_at or ""))
            return max(0, round((datetime.now().astimezone() - started).total_seconds() * 1000))
        except ValueError:
            return None

    def _append_execution_event(
        self, task: dict[str, Any], message: str, *, level: str = "info",
        stage_code: str = "",
    ) -> None:
        execution = task.setdefault("execution", {})
        events = list(execution.get("events") or [])
        events.append({
            "at": datetime.now().astimezone().isoformat(),
            "level": level,
            "stage_code": stage_code or str(execution.get("stage_code") or ""),
            "message": message,
        })
        execution["events"] = events[-16:]

    def _begin_execution(self, task: dict[str, Any], *, trigger: str) -> None:
        now = datetime.now().astimezone().isoformat()
        execution = task.setdefault("execution", {})
        execution.update({
            "state": "running",
            "stage": "准备检查",
            "stage_code": "starting",
            "trigger": trigger,
            "check_started_at": now,
            "stage_started_at": now,
            "last_error_stage": "",
            "last_error_type": "",
            "last_error_message": "",
        })
        self._append_execution_event(
            task,
            "手动立即检查已开始" if trigger == "manual" else "定时策略检查已开始",
            stage_code="starting",
        )
        self._persist_task(task)

    def _set_execution_stage(
        self, task: dict[str, Any], stage_code: str, message: str,
    ) -> None:
        now = datetime.now().astimezone().isoformat()
        execution = task.setdefault("execution", {})
        previous_duration = self._execution_elapsed_ms(execution.get("stage_started_at"))
        execution.update({
            "state": "running",
            "stage": message,
            "stage_code": stage_code,
            "stage_started_at": now,
            "previous_stage_duration_ms": previous_duration,
            "elapsed_ms": self._execution_elapsed_ms(execution.get("check_started_at")),
        })
        task["message"] = message
        self._append_execution_event(task, message, stage_code=stage_code)
        self._persist_task(task)

    def _finish_execution(self, task: dict[str, Any]) -> None:
        now = datetime.now().astimezone().isoformat()
        execution = task.setdefault("execution", {})
        duration = self._execution_elapsed_ms(execution.get("check_started_at"))
        execution.update({
            "state": "idle",
            "stage": task.get("message") or "检查完成",
            "stage_code": "complete",
            "last_completed_at": now,
            "last_duration_ms": duration,
            "elapsed_ms": duration,
        })
        duration_text = f"，耗时 {(duration or 0) / 1000:.1f} 秒" if duration is not None else ""
        self._append_execution_event(
            task, f"本轮检查结束{duration_text}：{task.get('message') or '已完成'}",
            stage_code="complete",
        )
        logger.info(
            "jwxk automation check completed task=%s trigger=%s duration_ms=%s status=%s",
            task.get("task_id"), execution.get("trigger"), duration, task.get("status"),
        )
        self._persist_task(task)

    def _fail_execution(self, task: dict[str, Any], error: Exception) -> None:
        now = datetime.now().astimezone().isoformat()
        execution = task.setdefault("execution", {})
        duration = self._execution_elapsed_ms(execution.get("check_started_at"))
        stage = str(execution.get("stage") or "未知阶段")
        execution.update({
            "state": "error",
            "last_completed_at": now,
            "last_duration_ms": duration,
            "elapsed_ms": duration,
            "last_error_stage": stage,
            "last_error_type": type(error).__name__,
            "last_error_message": str(error),
            "last_error_at": now,
        })
        self._append_execution_event(
            task, f"{stage}失败：{error}", level="error",
            stage_code=str(execution.get("stage_code") or "error"),
        )
        logger.warning(
            "jwxk automation check failed task=%s trigger=%s stage=%s duration_ms=%s error=%s",
            task.get("task_id"), execution.get("trigger"), execution.get("stage_code"),
            duration, type(error).__name__,
        )
        self._persist_task(task)

    def _task_auth(self, task: dict[str, Any]) -> Any | None:
        auth = self.auth_recover_provider()
        if (
            auth is None
            or str(getattr(auth, "username", "") or "") != str(task.get("account") or "")
        ):
            return None
        # JWXK is a child session of the shared identity but its bearer token
        # can remain valid while the primary JWXT route is unavailable.  Let
        # request_service validate or rebuild that child session instead of
        # rejecting it from the process-wide JWXT login flag here.
        return auth

    def _refresh_task_course_states(
        self, client: JwxkSessionClient, task: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        live_by_class: dict[str, dict[str, Any]] = {}
        with self._lock:
            archive = next((item for item in self._archives if (
                item.get("account") == task.get("account")
                and item.get("batch_code") == task.get("batch_code")
            )), None)
            archived_by_class = {
                str(item.get("class_id") or ""): item
                for item in (archive or {}).get("courses") or []
                if str(item.get("class_id") or "")
            }
            archive_sync_at = str((archive or {}).get("last_sync_at") or "")
            archive_sync_status = str((archive or {}).get("sync_status") or "")
        expected_task_ids = {
            str(item.get("class_id") or "")
            for item in task.get("items") or []
            if str(item.get("class_id") or "")
        }
        market_snapshot_at = str((task.get("weight_status") or {}).get("market_snapshot_at") or "")
        can_reuse_market_snapshot = bool(
            task.get("task_type") == "weight_strategy"
            and market_snapshot_at
            and market_snapshot_at == archive_sync_at
            and archive_sync_status == "complete"
        )
        if can_reuse_market_snapshot:
            live_by_class.update({
                class_id: archived_by_class[class_id]
                for class_id in expected_task_ids
                if class_id in archived_by_class
            })
            self._append_execution_event(
                task,
                f"复用刚完成的完整市场快照，方案候选精确命中 {len(live_by_class)}/{len(expected_task_ids)}",
                stage_code="market_refresh",
                level="error" if expected_task_ids - set(live_by_class) else "info",
            )
        query_specs: dict[tuple[str, str], dict[str, Any]] = {}
        for item in task.get("items") or []:
            item_class_id = str(item.get("class_id") or "")
            if item_class_id and item_class_id in live_by_class:
                continue
            query = str(item.get("course_code") or item.get("course_name") or "").strip()
            archived = archived_by_class.get(str(item.get("class_id") or ""), {})
            class_type = str(item.get("teaching_class_type") or "")
            if class_type in {"", "ALL", "ROUND", "ALLKC"}:
                class_type = str(archived.get("teaching_class_type") or "")
            if class_type in {"", "ALL", "ROUND", "ALLKC"}:
                class_type = next((
                    str(value) for value in archived.get("source_scopes") or []
                    if str(value) not in {"", "ALL", "ROUND", "ALLKC"}
                ), "ALLKC")
            query_key = (query.upper(), class_type)
            if not query:
                continue
            spec = query_specs.setdefault(query_key, {
                "query": query,
                "class_type": class_type,
                "class_ids": set(),
                "course_names": set(),
            })
            class_id = item_class_id
            if class_id:
                spec["class_ids"].add(class_id)
            course_name = str(item.get("course_name") or "").strip()
            if course_name:
                spec["course_names"].add(course_name)

        total_queries = len(query_specs)
        for index, spec in enumerate(query_specs.values(), start=1):
            query = str(spec["query"])
            class_type = str(spec["class_type"])
            expected_ids = set(spec["class_ids"])
            course_label = "、".join(sorted(spec["course_names"])) or query
            self._append_execution_event(
                task,
                f"刷新候选 {index}/{total_queries}：{course_label}（{class_type}）",
                stage_code="market_refresh",
            )
            started = time.monotonic()
            try:
                result = client.search_courses(
                    batch_code=task["batch_code"], teaching_class_type=class_type,
                    page_number=1, page_size=50, keyword=query,
                )
            except NEULoginError as error:
                raise NEULoginError(
                    f"刷新“{course_label}”人数时失败：{error}"
                ) from error
            except (JwxkError, requests.RequestException) as error:
                raise JwxkError(
                    f"刷新“{course_label}”人数时失败：{error}"
                ) from error
            rows = result.get("courses") or []
            matched_ids: set[str] = set()
            for row in rows:
                class_id = str(row.get("class_id") or "")
                # Keyword search can return adjacent or fuzzy-matched courses.
                # Only the exact teaching classes stored in this task are
                # valid model inputs; never let an unrelated result enter the
                # live snapshot.
                if class_id and class_id in expected_ids:
                    live_by_class[class_id] = row
                    matched_ids.add(class_id)
            missing_ids = sorted(expected_ids - matched_ids)
            elapsed_ms = round((time.monotonic() - started) * 1000)
            self._append_execution_event(
                task,
                f"候选 {index}/{total_queries} 返回 {len(rows)} 条，精确命中 {len(matched_ids)}/{len(expected_ids)}，耗时 {elapsed_ms} ms",
                stage_code="market_refresh",
                level="error" if missing_ids else "info",
            )
            logger.info(
                "jwxk strategy candidate refresh scope=%s result_count=%s exact_matches=%s expected=%s duration_ms=%s",
                class_type, len(rows), len(matched_ids), len(expected_ids), elapsed_ms,
            )
            if missing_ids:
                raise JwxkError(
                    f"刷新“{course_label}”人数时未找到方案中的教学班（{len(missing_ids)} 个），已停止本轮计算"
                )
        now = datetime.now().astimezone().isoformat()
        previous = task.get("course_states") if isinstance(task.get("course_states"), dict) else {}
        fields = (
            "class_id", "course_code", "course_name", "teacher", "capacity",
            "selected_count", "first_choice_count", "weight_participant_count",
            "market_participant_count", "market_participant_label", "full",
            "restricted", "eligibility_status", "eligibility_reason",
        )
        task["course_states"] = {
            str(item.get("class_id") or ""): {
                **dict(previous.get(str(item.get("class_id") or "")) or {}),
                **{
                    field: live_by_class[str(item.get("class_id") or "")].get(field)
                    for field in fields
                    if str(item.get("class_id") or "") in live_by_class
                    and field in live_by_class[str(item.get("class_id") or "")]
                },
                "updated_at": now if str(item.get("class_id") or "") in live_by_class else (
                    previous.get(str(item.get("class_id") or ""), {}).get("updated_at")
                ),
            }
            for item in task.get("items") or []
            if str(item.get("class_id") or "")
        }
        return live_by_class

    def _wait_for_auth(self, task: dict[str, Any], message: str = "正在自动恢复登录，恢复后继续任务") -> None:
        self._switch_to_vacancy_watch(task)
        task["status"] = "waiting"
        task["message"] = message
        task["last_attempt_at"] = datetime.now().astimezone().isoformat()
        task["updated_at"] = task["last_attempt_at"]
        self._persist_task(task)

    def _switch_to_vacancy_watch(self, task: dict[str, Any]) -> None:
        if task.get("polling_mode") != "opening_burst":
            return
        task["polling_mode"] = "vacancy_watch"
        task["poll_seconds"] = max(15, int(task.get("poll_seconds") or 15))
        task["message"] = "首轮抢选未完成，已切换为每 15 秒追踪空位"

    def _tick_catalog_sync(self) -> None:
        with self._lock:
            queued = next(iter(self._catalog_sync_queue), None)
            if queued is None:
                return
            self._catalog_sync_queue.discard(queued)
            account, batch_code = queued
            archive = next((copy.deepcopy(item) for item in self._archives if (
                item.get("account") == account and item.get("batch_code") == batch_code
            )), None)
        if archive is None:
            return
        auth = self.auth_recover_provider()
        if not auth or str(getattr(auth, "username", "") or "") != account:
            self._set_catalog_sync_state(account, batch_code, "paused", "登录身份不可用")
            return
        self._set_catalog_sync_state(account, batch_code, "running", "")
        current_scope = ""
        current_page = 0
        try:
            with self.remote_guard():
                if self.auth_recover_provider() is not auth:
                    raise NEULoginError("登录身份已切换")
                client = self.client_builder(auth)
                context = client.get_context()
            batch = next((item for item in context.get("batches") or [] if item.code == batch_code), None)
            if batch is None:
                raise JwxkError("选课轮次已不可见")
            scopes = list(dict.fromkeys(
                str(item.get("code") or "") for item in batch.menus
                if str(item.get("code") or "") not in {"", "ALL", "ROUND", "ALLKC"}
            ))
            loaded = 0
            total = 0
            for scope in scopes:
                current_scope = scope
                page = 1
                scope_total = None
                scope_started = time.monotonic()
                while page <= 200 and not self._stop.is_set():
                    current_page = page
                    self._update_catalog_sync_progress(
                        account, batch_code, scopes, loaded, total,
                        current_scope=scope, current_page=page,
                    )
                    page_started = time.monotonic()
                    with self.remote_guard():
                        if self.auth_recover_provider() is not auth:
                            raise NEULoginError("登录身份已切换")
                        result = client.search_courses(
                            batch_code=batch_code, teaching_class_type=scope,
                            page_number=page, page_size=50,
                        )
                    if scope_total is None:
                        scope_total = int(result.get("total") or 0)
                        total += scope_total
                    courses = result.get("courses") or []
                    groups = group_course_rows(
                        courses,
                        source_tags={
                            str(course.get("course_code") or ""): {
                                {"FANKC": "培养方案内课", "ALLKC": "全校课程查询"}.get(scope, scope)
                            }
                            for course in courses
                        },
                    )
                    self.merge_catalog_archive(account, batch=batch.to_dict(), scope=scope, groups=groups)
                    loaded += len(courses)
                    page_elapsed_ms = round((time.monotonic() - page_started) * 1000)
                    self._update_catalog_sync_progress(
                        account, batch_code, scopes, loaded, total,
                        current_scope=scope, current_page=page,
                    )
                    logger.info(
                        "jwxk round market refresh scope=%s page=%s rows=%s scope_total=%s loaded=%s total=%s duration_ms=%s",
                        scope, page, len(courses), scope_total, loaded, total, page_elapsed_ms,
                    )
                    if not courses or page * 50 >= scope_total:
                        break
                    page += 1
                logger.info(
                    "jwxk round market scope complete scope=%s rows=%s duration_ms=%s",
                    scope, scope_total or 0, round((time.monotonic() - scope_started) * 1000),
                )
            self._set_catalog_sync_state(account, batch_code, "complete", "")
        except JwxkRateLimitError as error:
            location = f"{current_scope} 第 {current_page} 页" if current_scope else "读取轮次上下文"
            self._set_catalog_sync_state(
                account, batch_code, "failed", f"{location}：{error}",
                retry_after_seconds=error.retry_after,
            )
        except (NEULoginError, JwxkError, requests.RequestException, ValueError) as error:
            location = f"{current_scope} 第 {current_page} 页" if current_scope else "读取轮次上下文"
            self._set_catalog_sync_state(
                account, batch_code, "failed", f"{location}：{error}",
            )

    def _update_catalog_sync_progress(
        self, account: str, batch_code: str, scopes: list[str], loaded: int, total: int,
        *, current_scope: str = "", current_page: int = 0,
    ) -> None:
        with self._lock:
            archive = next((item for item in self._archives if (
                item.get("account") == account and item.get("batch_code") == batch_code
            )), None)
            if archive is None:
                return
            archive.update({
                "sync_scopes": list(scopes), "sync_loaded": loaded,
                "sync_total": total, "updated_at": datetime.now().astimezone().isoformat(),
                "sync_current_scope": current_scope,
                "sync_current_page": current_page,
            })
            self._write_archives()

    def _set_catalog_sync_state(
        self, account: str, batch_code: str, status: str, error: str,
        *, retry_after_seconds: int | None = None,
    ) -> None:
        with self._lock:
            archive = next((item for item in self._archives if (
                item.get("account") == account and item.get("batch_code") == batch_code
            )), None)
            if archive is None:
                return
            archive["sync_status"] = status
            archive["sync_error"] = error[:300]
            archive["updated_at"] = datetime.now().astimezone().isoformat()
            if status in {"failed", "paused"}:
                failures = int(archive.get("sync_failure_count") or 0) + 1
                delay = (
                    min(self._CATALOG_RETRY_MAX_SECONDS, max(1, int(retry_after_seconds)))
                    if retry_after_seconds is not None
                    else min(self._CATALOG_RETRY_MAX_SECONDS, 15 * (2 ** min(failures - 1, 5)))
                )
                archive["sync_failure_count"] = failures
                archive["sync_retry_at"] = (
                    datetime.now().astimezone() + timedelta(seconds=delay)
                ).isoformat()
            if status == "complete":
                archive["last_sync_at"] = archive["updated_at"]
                archive["catalog_complete"] = True
                archive["sync_failure_count"] = 0
                archive["sync_retry_at"] = ""
                archive["sync_current_scope"] = ""
                archive["sync_current_page"] = 0
            self._write_archives()

    @staticmethod
    def _archive_end_time(archive: dict[str, Any]) -> datetime | None:
        try:
            value = datetime.fromisoformat(str(archive.get("end_time") or ""))
            return value.astimezone() if value.tzinfo else value.astimezone()
        except ValueError:
            return None

    def _tick_catalog_archives(self) -> None:
        auth = self.auth_recover_provider()
        if not auth:
            return
        account = str(getattr(auth, "username", "") or "")
        now = datetime.now().astimezone()
        with self._lock:
            candidates = [
                copy.deepcopy(item) for item in self._archives
                if item.get("account") == account and not item.get("archived")
            ]
        for archive in candidates:
            end = self._archive_end_time(archive)
            if end is None:
                continue
            if now >= end - timedelta(hours=5) and now < end:
                self._notify_underfilled_warning(account, archive)
            if now >= end and archive.get("final_refresh_status") == "complete":
                self._notify_round_end_summary(account, archive)
                self._finish_archive(account, archive["archive_id"])
                continue
            if now < end - timedelta(minutes=10) or now > end + timedelta(minutes=15):
                if now > end + timedelta(minutes=15):
                    self._notify_round_end_summary(account, archive)
                    self._finish_archive(account, archive["archive_id"])
                continue
            last_attempt = archive.get("final_refresh_attempt_at")
            if last_attempt:
                try:
                    if now - datetime.fromisoformat(last_attempt).astimezone() < timedelta(minutes=5):
                        continue
                except ValueError:
                    pass
            self._refresh_archive_counts(auth, archive)

    def _notify_round_end_summary(self, account: str, archive: dict[str, Any]) -> None:
        settings = self.get_automation_settings(account, str(archive.get("batch_code") or ""))
        if not settings.get("mail_enabled") or not settings.get("notify_round_end"):
            return
        key = f"{account}:{archive.get('batch_code')}:round_end_summary_sent"
        with self._lock:
            if self._notification_state.get(key):
                return
            rows = []
            selection_type = str(archive.get("selection_type_code") or "")
            for course in archive.get("courses") or []:
                if str(course.get("teaching_class_type") or "") == "ALLKC":
                    continue
                count = course.get("weight_participant_count") if selection_type == "04" else course.get("selected_count")
                rows.append(f"- {course.get('course_name') or '课程'}-{course.get('course_code') or ''}：{('已投注人数' if selection_type == '04' else '已选人数')} {count if count is not None else '待更新'} / 容量 {course.get('capacity') or '待更新'}")
        queued = self._queue_notification(
            account, str(archive.get("batch_code") or ""),
            f"JWXK 轮次结束总结 · {archive.get('batch_name') or archive.get('batch_code')}",
            f"轮次：{archive.get('batch_name') or archive.get('batch_code')}\n学期：{archive.get('term_code') or ''}\n\n" + ("\n".join(rows) or "无课程数据"),
            "round-end-summary",
        )
        if queued:
            with self._lock:
                self._notification_state[key] = datetime.now().astimezone().isoformat()
                self._write_json_map(self.notification_state_path, self._notification_state)

    def _notify_underfilled_warning(self, account: str, archive: dict[str, Any]) -> None:
        settings = self.get_automation_settings(account, str(archive.get("batch_code") or ""))
        if not settings.get("mail_enabled") or not settings.get("notify_underfilled_warning"):
            return
        selection_type = str(archive.get("selection_type_code") or "")
        rows = []
        with self._lock:
            for course in archive.get("courses") or []:
                if str(course.get("teaching_class_type") or "") == "ALLKC":
                    continue
                count = course.get("weight_participant_count") if selection_type == "04" else course.get("selected_count")
                if count is not None and int(count) < 10:
                    key = f"{account}:{archive.get('batch_code')}:underfilled:{course.get('class_id')}"
                    if self._notification_state.get(key):
                        continue
                    rows.append((course, key, int(count)))
        if not rows:
            return
        body = "轮次结束前 5 小时人数不足十人，仅为开课风险提示，不代表官方最终停开决定。\n\n" + "\n".join(
            f"- {course.get('course_name')}-{course.get('course_code')}：{count} / 容量 {course.get('capacity') or '待更新'}"
            for course, _, count in rows
        )
        if self._queue_notification(account, str(archive.get("batch_code") or ""), "JWXK 课程开课风险提示", body, "underfilled-warning"):
            with self._lock:
                for _, key, _ in rows:
                    self._notification_state[key] = datetime.now().astimezone().isoformat()
                self._write_json_map(self.notification_state_path, self._notification_state)

    def _finish_archive(self, account: str, archive_id: str) -> None:
        with self._lock:
            archive = next((item for item in self._archives if item.get("account") == account and item.get("archive_id") == archive_id), None)
            if archive is None:
                return
            archive["archived"] = True
            archive["updated_at"] = datetime.now().astimezone().isoformat()
            self._write_archives()

    def _refresh_archive_counts(self, auth: Any, archive: dict[str, Any]) -> None:
        archive_id = str(archive.get("archive_id") or "")
        targets: dict[tuple[str, str], list[str]] = {}
        for course in archive.get("courses") or []:
            scope = str(course.get("teaching_class_type") or "ALLKC")
            if course.get("eligibility_status") == "unavailable":
                continue
            if scope == "ALLKC" and course.get("eligibility_status") != "selectable":
                continue
            course_code = str(course.get("course_code") or "")
            class_id = str(course.get("class_id") or "")
            if course_code and class_id:
                targets.setdefault((scope, course_code), []).append(class_id)
        attempt_at = datetime.now().astimezone().isoformat()
        refreshed: dict[str, dict[str, Any]] = {}
        try:
            client = self.client_builder(auth)
            for (scope, course_code), class_ids in targets.items():
                if self._stop.is_set():
                    return
                with self.remote_guard():
                    if self.auth_provider() is not auth:
                        raise NEULoginError("登录身份已切换")
                    result = client.search_courses(
                        batch_code=archive["batch_code"],
                        teaching_class_type=scope,
                        page_number=1,
                        page_size=50,
                        keyword=course_code,
                    )
                for course in result.get("courses") or []:
                    class_id = str(course.get("class_id") or "")
                    if class_id in class_ids:
                        refreshed[class_id] = course
            status = "complete"
        except (NEULoginError, JwxkError, requests.RequestException, ValueError):
            status = "failed"
        with self._lock:
            target = next((item for item in self._archives if item.get("account") == archive.get("account") and item.get("archive_id") == archive_id), None)
            if target is None:
                return
            for course in target.get("courses") or []:
                latest = refreshed.get(str(course.get("class_id") or ""))
                if latest:
                    for key in (*self._CATALOG_DYNAMIC_FIELDS, "market_participant_label"):
                        if key in latest:
                            course[key] = latest.get(key)
                    course["capacity_updated_at"] = datetime.now().astimezone().isoformat()
            self._notify_capacity_transitions(target)
            target["final_refresh_attempt_at"] = attempt_at
            target["final_refresh_status"] = status
            if status == "complete":
                target["final_refresh_at"] = datetime.now().astimezone().isoformat()
            target["updated_at"] = datetime.now().astimezone().isoformat()
            self._write_archives()

    def _notify_capacity_transitions(self, archive: dict[str, Any]) -> None:
        account = str(archive.get("account") or "")
        batch_code = str(archive.get("batch_code") or "")
        settings = self.get_automation_settings(account, batch_code)
        if not settings.get("mail_enabled") or not (settings.get("notify_capacity_transition") or settings.get("notify_over_capacity")):
            return
        selection_type = str(archive.get("selection_type_code") or "")
        changed = []
        with self._lock:
            for course in archive.get("courses") or []:
                if str(course.get("teaching_class_type") or "") == "ALLKC":
                    continue
                capacity = int(course.get("capacity") or 0)
                count_value = course.get("weight_participant_count") if selection_type == "04" else course.get("selected_count")
                if capacity <= 0 or count_value is None:
                    continue
                count = int(count_value)
                ratio = max(0.0, (count - capacity) / capacity)
                state = "over" if count > capacity else "full" if count == capacity else "under"
                key = f"{account}:{batch_code}:capacity:{course.get('class_id')}"
                previous = self._notification_state.get(key) or {}
                if previous.get("state") == "under" and state in {"full", "over"}:
                    changed.append((course, previous, state, ratio))
                elif previous.get("state") in {"under", "full"} and ratio >= float(settings.get("over_capacity_ratio") or 0.2) and settings.get("notify_over_capacity"):
                    changed.append((course, previous, state, ratio))
                self._notification_state[key] = {"state": state, "count": count, "capacity": capacity, "ratio": ratio}
            self._write_json_map(self.notification_state_path, self._notification_state)
        for course, previous, state, ratio in changed:
            if state in {"full", "over"} and (state != "over" or settings.get("notify_over_capacity") or settings.get("notify_capacity_transition")):
                self._queue_notification(
                    account, batch_code,
                    f"JWXK 人数状态变化 · {course.get('course_name') or course.get('course_code')}",
                    f"课程：{course.get('course_name')}-{course.get('course_code')}\n旧状态：{previous.get('state')}\n新状态：{state}\n人数：{course.get('weight_participant_count') if selection_type == '04' else course.get('selected_count')} / 容量：{course.get('capacity')}\n超额比例：{ratio:.1%}",
                    f"capacity:{course.get('class_id')}:{state}:{int(ratio * 10000)}",
                )

    @classmethod
    def _inside_window(cls, task: dict[str, Any]) -> bool:
        now = datetime.now(_OFFICIAL_TIMEZONE)
        start = cls._parse_task_time(task.get("start_at"))
        end = cls._parse_task_time(task.get("end_at"))
        if task.get("start_at") and start is None:
            return False
        if task.get("end_at") and end is None:
            return False
        if start and now < start:
            return False
        if end and now > end:
            return False
        return True

    def _tick(self, task: dict[str, Any]) -> None:
        if task.get("desired_state") != "running" or task.get("status") not in {"running", "waiting"}:
            return
        with self._lock:
            archive = next((item for item in self._archives if (
                item.get("account") == task.get("account")
                and item.get("batch_code") == task.get("batch_code")
            )), None)
        selection_type_code = str((archive or {}).get("selection_type_code") or "")
        if selection_type_code:
            if task.get("task_type") == "weight_strategy" and selection_type_code != "04":
                self._set_state(task, "needs_review", "该任务不属于权重选课轮次，已停止执行")
                return
            if task.get("task_type") in {"selection", "vacancy_swap"} and selection_type_code != "02":
                self._set_state(task, "needs_review", "该任务不属于抢选轮次，已停止执行")
                return
        if task.get("task_type") == "vacancy_swap":
            self._tick_vacancy_swap(task)
            return
        if task.get("task_type") == "weight_strategy":
            self._tick_weight_strategy(task)
            return
        if not self._inside_window(task):
            if self._before_start(task):
                task["status"] = "waiting"
                task["message"] = "等待轮次开放，开放后将立即开始抢选"
                self._persist_task(task)
            return
        last = task.get("last_attempt_at")
        if last:
            try:
                elapsed = time.time() - datetime.fromisoformat(last).timestamp()
                if elapsed < self._poll_interval(task):
                    return
            except ValueError:
                pass
        mutation_started = False
        submitted_any = False
        try:
            with self.remote_guard():
                auth = self._task_auth(task)
                if auth is None:
                    self._wait_for_auth(task)
                    return
                client = self.client_builder(auth)
                task["status"] = "running"
                grouped: dict[str, list[dict[str, Any]]] = {}
                for item in task.get("items") or []:
                    group_id = str(item.get("plan_group_id") or item.get("course_code") or item.get("class_id") or "")
                    if group_id:
                        grouped.setdefault(group_id, []).append(item)
                specs = {spec["group_id"]: spec for spec in self._group_specs(task)}
                group_results = task.setdefault("group_results", {})
                official = client.get_selected(batch_code=task["batch_code"])
                confirmed_rows = [*(official.get("selected") or []), *(official.get("volunteered") or [])]
                confirmed_by_class = {
                    str(row.get("class_id") or ""): row
                    for row in confirmed_rows
                    if str(row.get("class_id") or "")
                }
                confirmed_by_code = {
                    str(row.get("course_code") or "").casefold(): row
                    for row in confirmed_rows
                    if str(row.get("course_code") or "")
                }
                inflight = task.get("inflight_mutation")
                if isinstance(inflight, dict):
                    class_id = str(inflight.get("class_id") or "")
                    course_code = str(inflight.get("course_code") or "").casefold()
                    if class_id in confirmed_by_class or (course_code and course_code in confirmed_by_code):
                        task["inflight_mutation"] = None
                    else:
                        self._set_state(task, "needs_review", "程序重启或登录恢复前有一项提交结果不明确，请先核验官方已选结果")
                        return
                live_by_class = self._refresh_task_course_states(client, task)
                for row in confirmed_rows:
                    class_id = str(row.get("class_id") or "")
                    if class_id in task.get("course_states", {}):
                        task["course_states"][class_id]["devoted_weight"] = row.get("devoted_weight")
                        task["course_states"][class_id]["selected"] = True
                for group_id, alternatives in grouped.items():
                    spec = specs.get(group_id) or {
                        "group_id": group_id, "name": "方案组", "target_count": 1,
                    }
                    result = group_results.setdefault(group_id, self._empty_group_result(spec))
                    target_count = max(1, int(spec.get("target_count") or 1))
                    confirmed = []
                    confirmed_keys = set()
                    for item in alternatives:
                        row = confirmed_by_class.get(str(item.get("class_id") or "")) or confirmed_by_code.get(
                            str(item.get("course_code") or "").casefold()
                        )
                        if not row:
                            continue
                        course_key = str(item.get("course_code") or item.get("class_id") or "")
                        if not course_key or course_key in confirmed_keys:
                            continue
                        confirmed_keys.add(course_key)
                        confirmed.append({
                            "class_id": row.get("class_id") or item.get("class_id") or "",
                            "course_code": item.get("course_code") or "",
                            "course_name": item.get("course_name") or item.get("course_code") or "",
                        })
                    result["selected"] = confirmed
                    result["success_count"] = len(confirmed_keys)
                    result["target_count"] = target_count
                    pending = [
                        class_id for class_id in result.get("pending_class_ids") or []
                        if class_id not in confirmed_by_class
                    ]
                    result["pending_class_ids"] = pending
                    if result["success_count"] >= target_count:
                        result["status"] = "success"
                        result["message"] = f"已选中 {result['success_count']}/{target_count} 门"
                        continue
                    if pending:
                        checks = int(result.get("pending_checks") or 0) + 1
                        result["pending_checks"] = checks
                        result["status"] = "verifying"
                        result["message"] = f"已有提交等待官方确认，当前 {result['success_count']}/{target_count} 门"
                        if checks >= 3:
                            result["status"] = "needs_review"
                            self._set_state(task, "needs_review", f"方案组“{spec['name']}”的提交长时间未确认，请人工核验")
                            return
                        continue
                    result["pending_checks"] = 0
                    for item in sorted(alternatives, key=lambda value: value.get("priority", 999)):
                        course_key = str(item.get("course_code") or item.get("class_id") or "")
                        if course_key in confirmed_keys or str(item.get("course_code") or "").casefold() in confirmed_by_code:
                            continue
                        candidate = live_by_class.get(str(item.get("class_id") or ""))
                        if not candidate or candidate.get("full") or candidate.get("restricted") or candidate.get("conflict"):
                            continue
                        eligibility = client.check_course_eligibility(
                            batch_code=task["batch_code"],
                            class_ids=[item["class_id"]],
                        ).get("results", [{}])[0]
                        if eligibility.get("status") == "unavailable":
                            continue
                        if eligibility.get("status") != "selectable":
                            group_results[group_id].update({
                                "status": "needs_review",
                                "message": eligibility.get("reason") or "教学班可选性暂时无法确认",
                            })
                            self._set_state(task, "needs_review", f"方案组“{spec['name']}”的教学班可选性需要核验")
                            return
                        if task.get("status") != "running":
                            return
                        task["inflight_mutation"] = {
                            "action": "select", "group_id": group_id,
                            "class_id": item["class_id"], "course_code": item["course_code"],
                            "started_at": datetime.now().astimezone().isoformat(),
                        }
                        self._persist_task(task)
                        mutation_started = True
                        mutation = client.select_course(
                            batch_code=task["batch_code"],
                            teaching_class_type=item.get("teaching_class_type") or "ALLKC",
                            class_id=item["class_id"], course_code=item["course_code"],
                            weight=item.get("weight"), confirm_risk=False,
                        )
                        record = {
                            "group_id": group_id, "class_id": item["class_id"],
                            "course_code": item.get("course_code") or "",
                            "course_name": item.get("course_name") or "",
                            "code": mutation.get("code"), "message": mutation.get("message"),
                            "at": datetime.now().astimezone().isoformat(),
                        }
                        task.setdefault("results", []).append(record)
                        if mutation.get("success"):
                            group_result = group_results[group_id]
                            group_result["status"] = "verifying"
                            group_result["pending_class_ids"] = [item["class_id"]]
                            group_result["pending_checks"] = 0
                            group_result["message"] = (
                                f"已提交“{item.get('course_name') or item.get('course_code')}”，"
                                f"等待官方确认；当前 {group_result['success_count']}/{target_count} 门"
                            )
                            task["inflight_mutation"] = None
                            submitted_any = True
                            self._persist_task(task)
                            break
                        task["inflight_mutation"] = None
                        self._persist_task(task)
                        group_results[group_id].update({
                            "status": "needs_review",
                            "message": mutation.get("message") or "提交结果需要核验",
                        })
                        self._set_state(task, "needs_review", f"方案组“{spec['name']}”需要核验")
                        return
                if grouped and all((group_results.get(group_id) or {}).get("status") == "success" for group_id in grouped):
                    total = sum(int(value.get("success_count") or 0) for value in group_results.values())
                    self._set_state(task, "success", f"{len(grouped)} 个方案组均已达标，共选中 {total} 门课程")
                    return
                if task.get("polling_mode") == "opening_burst" and not submitted_any:
                    self._switch_to_vacancy_watch(task)
            task["last_attempt_at"] = datetime.now().astimezone().isoformat()
            task["attempt_count"] = int(task.get("attempt_count") or 0) + 1
            completed = sum(1 for value in (task.get("group_results") or {}).values() if value.get("status") == "success")
            slots = sum(int(value.get("success_count") or 0) for value in (task.get("group_results") or {}).values())
            targets = sum(int(value.get("target_count") or 1) for value in (task.get("group_results") or {}).values())
            task["message"] = f"已完成 {completed}/{len(task.get('group_results') or {})} 个方案组，已选 {slots}/{targets} 门"
            self._persist_task(task)
        except NEULoginError:
            if mutation_started:
                self._set_state(task, "needs_review", "提交期间登录状态变化，结果不明确，请核验官方已选结果")
            else:
                self._wait_for_auth(task)
        except (requests.RequestException, JwxkError) as error:
            if mutation_started:
                self._set_state(task, "needs_review", f"提交结果不明确，请核验官方已选结果：{error}")
            else:
                task["status"] = "waiting"
                task["message"] = f"读取选课状态失败，将自动重试：{error}"
                self._switch_to_vacancy_watch(task)
                task["last_attempt_at"] = datetime.now().astimezone().isoformat()
                self._persist_task(task)

    @staticmethod
    def _weight_task_targets(task: dict[str, Any], live_by_class: dict[str, dict]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for raw in task.get("items") or []:
            item = {**raw, **live_by_class.get(str(raw.get("class_id") or ""), {})}
            key = str(item.get("course_code") or item.get("class_id") or "").strip().upper()
            if key:
                grouped.setdefault(key, []).append(item)
        targets = []
        for course_key, alternatives in grouped.items():
            alternatives.sort(key=lambda value: (
                int(value.get("priority") or 999), str(value.get("class_id") or ""),
            ))
            representative = alternatives[0]
            targets.append({
                **representative,
                "course_key": course_key,
                "model_id": str(representative.get("class_id") or course_key),
                "alternatives": alternatives,
                "capacity": max(1, max(int(item.get("capacity") or 0) for item in alternatives)),
                "participants": max(
                    int(item.get("weight_participant_count") or item.get("market_participant_count") or 0)
                    for item in alternatives
                ),
                "utility": max(float(item.get("utility") or 5) for item in alternatives),
                "group_ids": sorted({
                    str(item.get("plan_group_id") or "") for item in alternatives
                    if str(item.get("plan_group_id") or "")
                }),
                "time_unknown": any(
                    not item.get("schedules")
                    or any(meeting.get("recurrence_unknown") for meeting in item.get("schedules") or [])
                    for item in alternatives
                ),
            })
        return targets

    @staticmethod
    def _weight_task_conflicts(targets: list[dict[str, Any]], term_code: str) -> list[tuple[str, str]]:
        normalized: dict[str, list] = {}
        for target in targets:
            normalized[target["model_id"]] = [normalize_meeting({
                **meeting,
                "source_id": target["model_id"],
                "teaching_class_id": target["model_id"],
                "course_code": target.get("course_code") or target["course_key"],
                "course_name": target.get("course_name") or target["course_key"],
            }, term_code=term_code, default_source="jwxk_weight_task") for meeting in target.get("schedules") or []]
        conflicts = []
        for index, left in enumerate(targets):
            for right in targets[index + 1:]:
                if not normalized[left["model_id"]] or not normalized[right["model_id"]]:
                    continue
                checked = check_conflicts(
                    normalized[left["model_id"]], normalized[right["model_id"]],
                    ignore_same_course=True,
                )
                if any(item.status.value == "conflict" for item in checked):
                    conflicts.append((left["model_id"], right["model_id"]))
        return conflicts

    def _tick_weight_strategy(self, task: dict[str, Any]) -> None:
        if task.get("desired_state") != "running" or task.get("status") not in {"running", "waiting"}:
            return
        if not self._inside_window(task):
            if self._before_start(task):
                task["status"] = "waiting"
                task["message"] = "等待权重轮次开放，开放后自动开始实时策略"
                self._persist_task(task)
            return
        weight_status = task.setdefault("weight_status", {})
        settings = self.get_automation_settings(str(task.get("account") or ""), str(task.get("batch_code") or ""))
        end = self._parse_task_time(task.get("end_at"))
        now = datetime.now(_OFFICIAL_TIMEZONE)
        final_mode = str(settings.get("strategy_schedule_mode") or "interval") == "final_windows"
        window_key = ""
        if end and now >= end - timedelta(minutes=3) and now <= end:
            window_key = "final_3"
        elif end and now >= end - timedelta(minutes=5) and now < end - timedelta(minutes=3):
            window_key = "final_5"
        force_final = bool(settings.get("force_final_rebalance", True)) and bool(end) and now >= end - timedelta(minutes=3) and now <= end
        if final_mode and window_key and not weight_status.get(f"{window_key}_executed"):
            weight_status["final_window_requested"] = window_key
            task["message"] = f"已进入轮次结束前 {5 if window_key == 'final_5' else 3} 分钟策略窗口，正在读取最新人数"
            task["last_attempt_at"] = None
            self._append_execution_event(task, f"触发轮次结束前 {5 if window_key == 'final_5' else 3} 分钟策略", stage_code=window_key)
            self._persist_task(task)
        elif not final_mode and force_final and not weight_status.get("final_rebalance_executed"):
            weight_status["final_rebalance_requested"] = True
            task["message"] = "已进入轮次结束前 3 分钟强制策略窗口，正在读取最新人数"
            task["last_attempt_at"] = None
            self._append_execution_event(task, "触发轮次结束前 3 分钟强制策略", stage_code="final_rebalance")
            self._persist_task(task)
        busy = bool(
            weight_status.get("inflight")
            or weight_status.get("pending_drop")
            or weight_status.get("pending_add")
        )
        market_refresh_pending = bool(weight_status.get("market_refresh_pending"))
        market_snapshot_ready = False
        if market_refresh_pending and not busy and self._catalog_thread is not None:
            # The task scheduler remains responsive while the catalog worker
            # is blocked in a network call, so it also acts as the watchdog
            # that resets a scan whose progress timestamp stopped moving.
            self._reset_stale_catalog_syncs()
            with self._lock:
                pending_archive = next((copy.deepcopy(item) for item in self._archives if (
                    item.get("account") == task.get("account")
                    and item.get("batch_code") == task.get("batch_code")
                )), None)
            sync_status = str((pending_archive or {}).get("sync_status") or "")
            if sync_status in {"queued", "running", "pending"}:
                task["status"] = "running"
                current_scope = str((pending_archive or {}).get("sync_current_scope") or "")
                current_page = int((pending_archive or {}).get("sync_current_page") or 0)
                loaded = int((pending_archive or {}).get("sync_loaded") or 0)
                total = int((pending_archive or {}).get("sync_total") or 0)
                progress = f"，已读取 {loaded}/{total or '?'}"
                location = f"（{current_scope} 第 {current_page} 页）" if current_scope else ""
                task["message"] = f"正在刷新当前轮次全部真实课程类别的人数{location}{progress}，完成后生成策略"
                self._persist_task(task)
                return
            if sync_status in {"failed", "paused"}:
                reason = str((pending_archive or {}).get("sync_error") or "学校接口暂时不可用")
                retry_at = str((pending_archive or {}).get("sync_retry_at") or "")
                try:
                    retry_seconds = max(0, round((
                        datetime.fromisoformat(retry_at).astimezone()
                        - datetime.now().astimezone()
                    ).total_seconds()))
                except ValueError:
                    retry_seconds = 0
                failure_key = f"{sync_status}:{reason}:{retry_at}"
                if weight_status.get("market_last_failure") != failure_key:
                    weight_status["market_last_failure"] = failure_key
                    self._append_execution_event(
                        task,
                        f"完整市场读取失败，已自动重置并等待重试：{reason}",
                        level="error", stage_code="full_market_refresh",
                    )
                task["status"] = "running"
                task["message"] = (
                    f"完整市场读取失败：{reason}；已静默重置，"
                    f"约 {retry_seconds} 秒后自动重试"
                )
                self._persist_task(task)
                return
            latest_sync = str((pending_archive or {}).get("last_sync_at") or "")
            previous_sync = str(weight_status.get("market_refresh_previous_sync") or "")
            if sync_status == "complete" and latest_sync and latest_sync != previous_sync:
                weight_status.pop("market_refresh_pending", None)
                weight_status.pop("market_refresh_previous_sync", None)
                weight_status["market_snapshot_at"] = latest_sync
                weight_status.pop("market_last_failure", None)
                market_snapshot_ready = True
                self._append_execution_event(
                    task,
                    f"当前轮次市场快照已更新：{int((pending_archive or {}).get('sync_loaded') or 0)} 条教学班",
                    stage_code="full_market_refresh",
                )
            else:
                task["status"] = "running"
                task["message"] = "等待当前轮次市场快照完成写入"
                self._persist_task(task)
                return
        manual_check = bool(task.get("manual_check_requested_at"))
        last = task.get("last_attempt_at")
        window_requested = bool(weight_status.get("final_window_requested"))
        if final_mode and not window_requested and not market_snapshot_ready:
            if not last:
                pass
            else:
                try:
                    interval = max(600, int(settings.get("rebalance_seconds") or task.get("rebalance_seconds") or 600))
                    if time.time() - datetime.fromisoformat(last).timestamp() < interval:
                        return
                except ValueError:
                    pass
                return
        if last and not busy and not manual_check and not market_snapshot_ready and not weight_status.get("final_rebalance_requested") and not window_requested:
            try:
                interval = max(600, int(settings.get("rebalance_seconds") or task.get("rebalance_seconds") or 600))
                if time.time() - datetime.fromisoformat(last).timestamp() < interval:
                    return
            except ValueError:
                pass
        if not market_snapshot_ready:
            manual_check = self._consume_manual_check(task)
            attempted_at = datetime.now().astimezone().isoformat()
            task["status"] = "running"
            task["last_attempt_at"] = attempted_at
            task["attempt_count"] = int(task.get("attempt_count") or 0) + 1
            self._begin_execution(task, trigger="manual" if manual_check else "scheduled")
            if self._catalog_thread is not None and not busy:
                with self._lock:
                    current_archive = next((copy.deepcopy(item) for item in self._archives if (
                        item.get("account") == task.get("account")
                        and item.get("batch_code") == task.get("batch_code")
                    )), None)
                if current_archive is not None:
                    self._set_execution_stage(
                        task, "full_market_refresh",
                        "正在刷新当前轮次全部真实课程类别的人数",
                    )
                    weight_status["market_refresh_pending"] = True
                    weight_status["market_refresh_previous_sync"] = str(
                        current_archive.get("last_sync_at") or ""
                    )
                    self.schedule_catalog_sync(
                        str(task.get("account") or ""),
                        batch={**current_archive, "code": str(task.get("batch_code") or "")},
                        force=True,
                    )
                    self._persist_task(task)
                    return
        mutation_started = False
        execution_failed = False
        try:
            with self.remote_guard():
                self._set_execution_stage(task, "authentication", "正在检查 JWXK 业务会话")
                auth = self._task_auth(task)
                if auth is None:
                    raise NEULoginError("当前账号的 JWXK 认证客户端不可用")
                client = self.client_builder(auth)
                task["status"] = "running"
                self._set_execution_stage(task, "selected_results", "正在读取官方已投结果和当前权重")
                official = client.get_selected(batch_code=task["batch_code"])
                volunteered = list(official.get("volunteered") or [])
                confirmed = list(official.get("selected") or [])
                official_by_class = {
                    str(item.get("class_id") or ""): item
                    for item in (*volunteered, *confirmed)
                    if str(item.get("class_id") or "")
                }
                official_by_code = {
                    str(item.get("course_code") or "").casefold(): item
                    for item in (*volunteered, *confirmed)
                    if str(item.get("course_code") or "")
                }
                course_states = task.setdefault("course_states", {})
                official_checked_at = datetime.now().astimezone().isoformat()
                for item in task.get("items") or []:
                    class_id = str(item.get("class_id") or "")
                    course_code = str(item.get("course_code") or "").casefold()
                    row = official_by_class.get(class_id) or official_by_code.get(course_code)
                    if row is None or not class_id:
                        continue
                    state = course_states.setdefault(class_id, {})
                    state.update({
                        "class_id": class_id,
                        "course_code": item.get("course_code") or row.get("course_code") or "",
                        "course_name": item.get("course_name") or row.get("course_name") or "",
                        "devoted_weight": row.get("devoted_weight"),
                        "selected": True,
                        "selection_updated_at": official_checked_at,
                    })
                self._persist_task(task)

                self._set_execution_stage(task, "reconcile", "正在核验上一次投权操作结果")
                persisted_inflight = task.get("inflight_mutation")
                if isinstance(persisted_inflight, dict) and not weight_status.get("inflight"):
                    class_id = str(persisted_inflight.get("class_id") or "")
                    action = str(persisted_inflight.get("action") or "")
                    row = official_by_class.get(class_id)
                    expected_weight = int(persisted_inflight.get("weight") or 0)
                    reconciled = (
                        action == "weight_drop" and row is None
                    ) or (
                        action == "weight_add" and row is not None
                        and (
                            row.get("devoted_weight") is None
                            or int(row.get("devoted_weight") or 0) == expected_weight
                        )
                    )
                    if not reconciled:
                        self._set_state(task, "needs_review", "程序重启或登录恢复前有一项投权写入结果不明确，请人工核验")
                        return
                    task["inflight_mutation"] = None
                    weight_status["last_adjusted_at"] = datetime.now().astimezone().isoformat()
                    self._persist_task(task)

                inflight = weight_status.get("inflight")
                if isinstance(inflight, dict):
                    class_id = str(inflight.get("class_id") or "")
                    row = official_by_class.get(class_id)
                    expected_weight = int(inflight.get("weight") or 0)
                    confirmed_action = (
                        inflight.get("action") == "drop" and row is None
                    ) or (
                        inflight.get("action") == "add" and row is not None
                        and (
                            row.get("devoted_weight") is None
                            or int(row.get("devoted_weight") or 0) == expected_weight
                        )
                    )
                    if confirmed_action:
                        queue_key = "pending_drop" if inflight.get("action") == "drop" else "pending_add"
                        weight_status[queue_key] = [
                            item for item in weight_status.get(queue_key) or []
                            if str(item.get("class_id") or "") != class_id
                        ]
                        weight_status["inflight"] = None
                        weight_status["confirmation_checks"] = 0
                        weight_status["last_adjusted_at"] = datetime.now().astimezone().isoformat()
                    else:
                        checks = int(weight_status.get("confirmation_checks") or 0) + 1
                        weight_status["confirmation_checks"] = checks
                        task["message"] = "投权调整已提交，正在核验官方结果"
                        if checks >= 3:
                            self._set_state(task, "needs_review", "投权调整连续三次未能从官方结果确认，请人工核验")
                            return
                        task["last_attempt_at"] = datetime.now().astimezone().isoformat()
                        self._persist_task(task)
                        return

                if weight_status.get("pending_drop"):
                    item = dict(weight_status["pending_drop"][0])
                    self._set_execution_stage(
                        task, "weight_drop",
                        f"正在撤回“{item.get('course_name') or item.get('course_code')}”的旧权重",
                    )
                    task["inflight_mutation"] = {
                        "action": "weight_drop", "class_id": item["class_id"],
                        "course_code": item.get("course_code") or "",
                        "started_at": datetime.now().astimezone().isoformat(),
                    }
                    self._persist_task(task)
                    mutation_started = True
                    result = client.deselect_course(
                        batch_code=task["batch_code"], class_id=item["class_id"],
                        confirm_risk=False,
                    )
                    task.setdefault("results", []).append({
                        "action": "weight_drop", "class_id": item["class_id"],
                        "course_code": item.get("course_code") or "",
                        "course_name": item.get("course_name") or "",
                        "code": result.get("code"), "message": result.get("message"),
                        "at": datetime.now().astimezone().isoformat(),
                    })
                    if not result.get("success"):
                        self._set_state(task, "needs_review", result.get("message") or "退选旧权重失败")
                        return
                    task["inflight_mutation"] = None
                    weight_status["inflight"] = {"action": "drop", **item}
                    weight_status["confirmation_checks"] = 0
                    task["message"] = f"正在确认撤回“{item.get('course_name') or item.get('course_code')}”的旧权重"
                    task["last_attempt_at"] = datetime.now().astimezone().isoformat()
                    self._persist_task(task)
                    return

                if weight_status.get("pending_add"):
                    item = dict(weight_status["pending_add"][0])
                    self._set_execution_stage(
                        task, "weight_add",
                        f"正在为“{item.get('course_name') or item.get('course_code')}”投放 {int(item['weight'])} 点权重",
                    )
                    task["inflight_mutation"] = {
                        "action": "weight_add", "class_id": item["class_id"],
                        "course_code": item.get("course_code") or "",
                        "weight": int(item["weight"]),
                        "started_at": datetime.now().astimezone().isoformat(),
                    }
                    self._persist_task(task)
                    mutation_started = True
                    result = client.select_course(
                        batch_code=task["batch_code"],
                        teaching_class_type=item.get("teaching_class_type") or "ALLKC",
                        class_id=item["class_id"], course_code=item["course_code"],
                        weight=int(item["weight"]), confirm_risk=False,
                    )
                    task.setdefault("results", []).append({
                        "action": "weight_add", "class_id": item["class_id"],
                        "course_code": item.get("course_code") or "",
                        "course_name": item.get("course_name") or "",
                        "weight": item["weight"], "code": result.get("code"),
                        "message": result.get("message"),
                        "at": datetime.now().astimezone().isoformat(),
                    })
                    if not result.get("success"):
                        self._set_state(task, "needs_review", result.get("message") or "重新投放权重失败")
                        return
                    task["inflight_mutation"] = None
                    weight_status["inflight"] = {"action": "add", **item}
                    weight_status["confirmation_checks"] = 0
                    task["message"] = f"正在确认“{item.get('course_name') or item.get('course_code')}”的新权重"
                    task["last_attempt_at"] = datetime.now().astimezone().isoformat()
                    self._persist_task(task)
                    return

                with self._lock:
                    archive = next((copy.deepcopy(item) for item in self._archives if (
                        item.get("account") == task.get("account")
                        and item.get("batch_code") == task.get("batch_code")
                    )), None)
                if not archive or not archive.get("courses") or not (
                    archive.get("catalog_complete") or archive.get("sync_status") == "complete"
                ):
                    task["status"] = "waiting"
                    task["message"] = "等待完整轮次课程数据同步后计算投权策略"
                    task["last_attempt_at"] = datetime.now().astimezone().isoformat()
                    self._persist_task(task)
                    return

                self._set_execution_stage(task, "market_refresh", "正在刷新方案组全部候选课程的已投注人数和容量")
                live_by_class = self._refresh_task_course_states(client, task)
                for row in (*volunteered, *confirmed):
                    class_id = str(row.get("class_id") or "")
                    if class_id in task.get("course_states", {}):
                        task["course_states"][class_id]["devoted_weight"] = row.get("devoted_weight")
                        task["course_states"][class_id]["selected"] = True

                targets = self._weight_task_targets(task, live_by_class)
                if not targets:
                    self._set_state(task, "needs_review", "方案组中没有可计算的投权课程")
                    return
                target_codes = {str(item["course_key"]).casefold() for item in targets}
                managed_current = [
                    item for item in volunteered
                    if str(item.get("course_code") or "").casefold() in target_codes
                ]
                self._set_execution_stage(task, "weight_budget", "正在读取官方剩余权重")
                budget_info = client.get_weight_budget(batch_code=task["batch_code"])
                total_available = int(budget_info["remaining"]) + sum(
                    int(item.get("devoted_weight") or 0) for item in managed_current
                )
                groups = [WeightGroupTarget(
                    str(item.get("group_id") or ""),
                    str(item.get("name") or "方案组"),
                    max(1, int(item.get("target_count") or 1)),
                ) for item in task.get("groups") or []]
                volunteered_codes = {
                    str(item.get("course_code") or "").casefold() for item in volunteered
                    if str(item.get("course_code") or "")
                }
                confirmed_codes = {
                    str(item.get("course_code") or "").casefold() for item in confirmed
                    if str(item.get("course_code") or "").casefold() not in volunteered_codes
                }
                market = [WeightMarketCourse(
                    course_id=str(item.get("class_id") or ""),
                    capacity=max(1, int(item.get("capacity") or 0)),
                    bidders=max(0, int(item.get("weight_participant_count") or item.get("market_participant_count") or 0)),
                ) for item in archive.get("courses") or [] if str(item.get("class_id") or "") and int(item.get("capacity") or 0) > 0]
                candidates = [WeightCandidate(
                    course_id=item["model_id"], name=str(item.get("course_name") or item["course_key"]),
                    capacity=int(item["capacity"]), bidders=int(item["participants"]),
                    utility=float(item["utility"]), group_ids=tuple(item["group_ids"]),
                    already_selected=str(item["course_key"]).casefold() in confirmed_codes,
                    time_unknown=bool(item["time_unknown"]),
                ) for item in targets]
                self._set_execution_stage(task, "optimization", "正在运行策略模型并生成本轮建议")
                optimized = optimize_grouped_weights(
                    policy=WeightPolicy(
                        budget=total_available,
                        min_bid=int(budget_info["minimum"]),
                        bid_step=int(budget_info["step"]),
                    ),
                    grade_size=int(task.get("grade_size") or 0),
                    market_courses=market,
                    candidates=candidates,
                    groups=groups,
                    conflicts=self._weight_task_conflicts(targets, str(task.get("term_code") or "")),
                )
                by_model = {item["model_id"]: item for item in targets}
                desired = [{
                    **by_model[result["course_id"]],
                    "weight": int(result["bid"]),
                } for result in optimized["courses"] if result["bid"] > 0 and not result["already_selected"]]
                desired_by_code = {str(item["course_key"]).casefold(): item for item in desired}
                current_by_code = {
                    str(item.get("course_code") or "").casefold(): item for item in managed_current
                }
                pending_drop = []
                pending_add = []
                for code, current in current_by_code.items():
                    target = desired_by_code.get(code)
                    if (
                        target is None
                        or str(current.get("class_id") or "") != str(target.get("class_id") or "")
                        or int(current.get("devoted_weight") or 0) != int(target.get("weight") or 0)
                    ):
                        pending_drop.append({
                            "class_id": str(current.get("class_id") or ""),
                            "course_code": str(current.get("course_code") or ""),
                            "course_name": str(current.get("course_name") or ""),
                        })
                for code, target in desired_by_code.items():
                    current = current_by_code.get(code)
                    if (
                        current is None
                        or str(current.get("class_id") or "") != str(target.get("class_id") or "")
                        or int(current.get("devoted_weight") or 0) != int(target.get("weight") or 0)
                    ):
                        pending_add.append({
                            "class_id": str(target.get("class_id") or ""),
                            "course_code": str(target.get("course_code") or target.get("course_key") or ""),
                            "course_name": str(target.get("course_name") or ""),
                            "teaching_class_type": str(target.get("teaching_class_type") or "ALLKC"),
                            "weight": int(target["weight"]),
                        })
                now = datetime.now().astimezone().isoformat()
            was_final_rebalance = bool(weight_status.get("final_rebalance_requested"))
            completed_window = str(weight_status.get("final_window_requested") or "")
            weight_status.update({
                    "last_calculated_at": now,
                    # Keep the complete model target, including courses whose
                    # current bid is already correct.  The UI must distinguish
                    # “keep current weight” from “no recommendation yet”.
                    "recommendation": desired,
                    "groups": optimized["groups"],
                    "warnings": optimized.get("warnings") or [],
                    "approximate": bool(optimized.get("approximate")),
                    "pending_drop": pending_drop,
                    "pending_add": pending_add,
                    "phase": "adjusting" if pending_drop or pending_add else "idle",
            })
            if was_final_rebalance:
                weight_status["final_rebalance_executed"] = True
                weight_status.pop("final_rebalance_requested", None)
                if settings.get("mail_enabled") and settings.get("notify_final_rebalance"):
                    rows = "\n".join(
                        f"- {item.get('course_name') or '课程'}-{item.get('course_code') or ''}：建议 {int(item.get('weight') or 0)}，已投注人数 {int(item.get('participants') or 0)} / 容量 {int(item.get('capacity') or 0)}"
                        for item in desired
                    ) or "无推荐投权课程"
                    self._queue_notification(
                        str(task.get("account") or ""), str(task.get("batch_code") or ""),
                        f"JWXK 强制策略结果 · {task.get('batch_code')}",
                        f"轮次结束前 3 分钟强制策略已完成。\n\n{rows}\n\n数据时间：{now}",
                        "final-rebalance",
                    )
            if completed_window:
                weight_status[f"{completed_window}_executed"] = True
                weight_status.pop("final_window_requested", None)
                if settings.get("mail_enabled") and settings.get("notify_final_rebalance"):
                    self._queue_notification(
                        str(task.get("account") or ""), str(task.get("batch_code") or ""),
                        f"JWXK {completed_window} 策略结果 · {task.get('batch_code')}",
                        f"轮次结束前窗口策略已完成：{completed_window}\n数据时间：{now}",
                        completed_window,
                    )
                task["group_results"] = {
                    item["group_id"]: {
                        "status": "success" if item["satisfied"] else "pending",
                        "message": "策略目标已覆盖" if item["satisfied"] else f"仍缺 {item['missing_count']} 门",
                        "target_count": item["target_count"],
                        "success_count": item["selected_count"],
                        "selected": [], "pending_class_ids": [],
                    }
                    for item in optimized["groups"]
                }
                task["last_attempt_at"] = now
                if pending_drop or pending_add:
                    task["message"] = f"已根据最新已投注人数重算，准备调整 {len(pending_drop)} 项旧权重并提交 {len(pending_add)} 项新权重"
                else:
                    task["message"] = "已根据最新已投注人数重算，当前权重无需调整"
                self._persist_task(task)
        except NEULoginError as error:
            execution_failed = True
            if mutation_started:
                self._set_state(task, "needs_review", "投权调整期间登录状态变化，结果不明确，请核验官方结果")
            else:
                stage = str((task.get("execution") or {}).get("stage") or "认证检查")
                self._wait_for_auth(task, f"{stage}失败：JWXK 会话需要恢复；下轮将从选课系统 CAS 入口重试")
            self._fail_execution(task, error)
        except (requests.RequestException, JwxkError, WeightOptimizationError, ValueError) as error:
            execution_failed = True
            if mutation_started:
                self._set_state(task, "needs_review", f"投权调整结果不明确，请核验官方结果：{error}")
            else:
                stage = str((task.get("execution") or {}).get("stage") or "策略检查")
                task["status"] = "waiting"
                task["message"] = f"{stage}失败，将按计划重试：{error}"
                task["last_attempt_at"] = datetime.now().astimezone().isoformat()
                self._persist_task(task)
            self._fail_execution(task, error)
        finally:
            if not execution_failed:
                self._finish_execution(task)

    def _tick_vacancy_swap(self, task: dict[str, Any]) -> None:
        if task.get("desired_state") != "running" or task.get("status") not in {"running", "waiting"}:
            return
        if not self._inside_window(task):
            return
        last = task.get("last_attempt_at")
        if last:
            try:
                if time.time() - datetime.fromisoformat(last).timestamp() < self._poll_interval(task):
                    return
            except ValueError:
                pass
        mutation_started = False
        try:
            with self.remote_guard():
                auth = self._task_auth(task)
                if auth is None:
                    self._wait_for_auth(task)
                    return
                client = self.client_builder(auth)
                task["status"] = "running"
                official = client.get_selected(batch_code=task["batch_code"])
                confirmed = [*(official.get("selected") or []), *(official.get("volunteered") or [])]
                selected_by_class = {str(row.get("class_id") or ""): row for row in confirmed}
                selected_codes = {
                    str(row.get("course_code") or "").casefold() for row in confirmed
                    if str(row.get("course_code") or "")
                }
                inflight = task.get("inflight_mutation")
                if isinstance(inflight, dict):
                    inflight_class = str(inflight.get("class_id") or "")
                    action = str(inflight.get("action") or "")
                    reconciled = (
                        action == "select" and inflight_class in selected_by_class
                    ) or (
                        action == "drop" and inflight_class not in selected_by_class
                    )
                    if reconciled:
                        task["inflight_mutation"] = None
                    else:
                        self._set_state(task, "needs_review", "程序重启或登录恢复前有一项换课提交结果不明确，请先核验官方结果")
                        return
                results = task.setdefault("swap_results", {})
                for group in task.get("swap_groups") or []:
                    group_id = str(group.get("group_id") or "")
                    target = dict(group.get("target") or {})
                    result = results.setdefault(group_id, {
                        "status": "monitoring", "message": "正在追踪空位",
                        "pending_drop_ids": [], "pending_checks": 0, "target_pending": False,
                    })
                    target_code = str(target.get("course_code") or "").casefold()
                    if target_code and target_code in selected_codes:
                        result.update({"status": "success", "message": "意向课程已经选中", "target_pending": False})
                        continue
                    if result.get("target_pending"):
                        checks = int(result.get("pending_checks") or 0) + 1
                        result["pending_checks"] = checks
                        result["status"] = "verifying"
                        result["message"] = "意向课程已提交，等待官方已选结果确认"
                        if checks >= 3:
                            self._set_state(task, "needs_review", f"空位组“{group.get('name') or group_id}”的选课结果长时间未确认")
                            return
                        continue
                    pending_drop_ids = list(result.get("pending_drop_ids") or [])
                    if pending_drop_ids:
                        remaining = [class_id for class_id in pending_drop_ids if class_id in selected_by_class]
                        if remaining:
                            checks = int(result.get("pending_checks") or 0) + 1
                            result.update({
                                "status": "verifying_drop", "pending_checks": checks,
                                "message": f"等待官方确认退选 {len(remaining)} 门课程",
                            })
                            if checks >= 3:
                                self._set_state(task, "needs_review", f"空位组“{group.get('name') or group_id}”的退选结果长时间未确认")
                                return
                            continue
                        result.update({"pending_drop_ids": [], "pending_checks": 0, "status": "monitoring", "message": "退选已确认，正在提交意向课程"})
                    catalog = client.search_courses(
                        batch_code=task["batch_code"],
                        teaching_class_type=target.get("teaching_class_type") or "ALLKC",
                        page_number=1, page_size=50,
                        keyword=target.get("course_code") or target.get("course_name") or "",
                    )
                    candidate = next((row for row in catalog.get("courses") or [] if row.get("class_id") == target.get("class_id")), None)
                    if candidate is None:
                        result.update({"status": "monitoring", "message": "暂未读取到意向教学班，稍后重试"})
                        continue
                    if candidate.get("full"):
                        result.update({"status": "monitoring", "message": "课程仍然满员，继续追踪空位"})
                        continue
                    drops = [
                        item for item in group.get("drop_courses") or []
                        if str(item.get("class_id") or "") in selected_by_class
                    ]
                    if drops:
                        submitted = []
                        for drop in drops:
                            if task.get("status") != "running":
                                return
                            task["inflight_mutation"] = {
                                "action": "drop", "group_id": group_id,
                                "class_id": drop["class_id"],
                                "course_code": drop.get("course_code") or "",
                                "started_at": datetime.now().astimezone().isoformat(),
                            }
                            self._persist_task(task)
                            mutation_started = True
                            mutation = client.deselect_course(
                                batch_code=task["batch_code"], class_id=drop["class_id"],
                                confirm_risk=False,
                            )
                            task.setdefault("results", []).append({
                                "group_id": group_id, "action": "drop", "class_id": drop["class_id"],
                                "course_code": drop.get("course_code") or "",
                                "course_name": drop.get("course_name") or "",
                                "code": mutation.get("code"), "message": mutation.get("message"),
                                "at": datetime.now().astimezone().isoformat(),
                            })
                            if not mutation.get("success"):
                                self._set_state(task, "needs_review", mutation.get("message") or "自动退选结果需要人工核验")
                                return
                            submitted.append(drop["class_id"])
                            task["inflight_mutation"] = None
                            self._persist_task(task)
                        result.update({
                            "status": "verifying_drop", "pending_drop_ids": submitted,
                            "pending_checks": 0, "message": f"已提交退选 {len(submitted)} 门课程，等待官方确认",
                        })
                        continue
                    eligibility = client.check_course_eligibility(
                        batch_code=task["batch_code"], class_ids=[target["class_id"]],
                    ).get("results", [{}])[0]
                    if eligibility.get("status") != "selectable":
                        if eligibility.get("status") == "unavailable":
                            result.update({"status": "monitoring", "message": eligibility.get("reason") or "当前仍不可选，继续追踪"})
                            continue
                        self._set_state(task, "needs_review", eligibility.get("reason") or "意向课程可选性无法确认")
                        return
                    if task.get("status") != "running":
                        return
                    task["inflight_mutation"] = {
                        "action": "select", "group_id": group_id,
                        "class_id": target["class_id"],
                        "course_code": target["course_code"],
                        "started_at": datetime.now().astimezone().isoformat(),
                    }
                    self._persist_task(task)
                    mutation_started = True
                    mutation = client.select_course(
                        batch_code=task["batch_code"],
                        teaching_class_type=target.get("teaching_class_type") or "ALLKC",
                        class_id=target["class_id"], course_code=target["course_code"],
                        weight=None, confirm_risk=False,
                    )
                    task.setdefault("results", []).append({
                        "group_id": group_id, "action": "select", "class_id": target["class_id"],
                        "course_code": target.get("course_code") or "",
                        "course_name": target.get("course_name") or "",
                        "code": mutation.get("code"), "message": mutation.get("message"),
                        "at": datetime.now().astimezone().isoformat(),
                    })
                    if not mutation.get("success"):
                        self._set_state(task, "needs_review", mutation.get("message") or "意向课程提交结果需要人工核验")
                        return
                    task["inflight_mutation"] = None
                    self._persist_task(task)
                    result.update({
                        "status": "verifying", "target_pending": True,
                        "pending_checks": 0, "message": "意向课程已提交，等待官方确认",
                    })
                if task.get("swap_groups") and all(value.get("status") == "success" for value in results.values()):
                    self._set_state(task, "success", "全部空位换课组均已完成")
                    return
            task["last_attempt_at"] = datetime.now().astimezone().isoformat()
            task["attempt_count"] = int(task.get("attempt_count") or 0) + 1
            task["message"] = f"正在追踪 {len(task.get('swap_groups') or [])} 个空位换课组"
            self._persist_task(task)
        except NEULoginError:
            if mutation_started:
                self._set_state(task, "needs_review", "换课提交期间登录状态变化，结果不明确，请核验官方结果")
            else:
                self._wait_for_auth(task)
        except (requests.RequestException, JwxkError) as error:
            if mutation_started:
                self._set_state(task, "needs_review", f"换课提交结果不明确，请核验官方结果：{error}")
            else:
                task["status"] = "waiting"
                task["message"] = f"读取空位失败，将自动重试：{error}"
                task["last_attempt_at"] = datetime.now().astimezone().isoformat()
                self._persist_task(task)

    def _set_state(self, task: dict[str, Any], status: str, message: str) -> None:
        task["status"] = status
        task["message"] = message
        task["updated_at"] = datetime.now().astimezone().isoformat()
        if task.get("task_type") in {"selection", "vacancy_swap"} and status in {"success", "needs_review"}:
            settings = self.get_automation_settings(str(task.get("account") or ""), str(task.get("batch_code") or ""))
            if settings.get("mail_enabled") and settings.get("notify_grab_result"):
                key = f"{task.get('account')}:{task.get('batch_code')}:{task.get('task_id')}:{status}"
                with self._lock:
                    already = self._notification_state.get(key)
                if not already and self._queue_notification(
                    str(task.get("account") or ""), str(task.get("batch_code") or ""),
                    f"JWXK 抢课任务{'成功' if status == 'success' else '待核验'} · {task.get('name') or task.get('task_id')}",
                    f"任务：{task.get('name') or task.get('task_id')}\n状态：{status}\n说明：{message}\n时间：{task.get('updated_at')}",
                    f"grab-result:{task.get('task_id')}:{status}",
                ):
                    with self._lock:
                        self._notification_state[key] = task.get("updated_at")
                        self._write_json_map(self.notification_state_path, self._notification_state)
        self._persist_task(task)

    def _persist_task(self, task: dict[str, Any]) -> None:
        with self._lock:
            task["updated_at"] = datetime.now().astimezone().isoformat()
            existing = next((item for item in self._tasks if item.get("task_id") == task.get("task_id")), None)
            if existing is not task and existing is not None:
                existing.update(task)
            self._write()
