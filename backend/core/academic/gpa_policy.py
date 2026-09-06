"""Account-owned GPA preferences and cache-only calculation context."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading

from backend.core.cache import CacheKey
from backend.core.runtime.config import secure_file


LEGACY = "through_2024"
MODERN = "from_2025"
MODES = (LEGACY, MODERN)


def default_mode(account: str) -> str:
    prefix = str(account)[:4]
    return MODERN if len(prefix) == 4 and prefix.isascii() and prefix.isdigit() and int(prefix) >= 2025 else LEGACY


def general_elective(course: dict) -> bool:
    return bool(course.get("gpa_general_elective")) or any(
        "通识选修" in str(course.get(field) or "")
        for field in ("course_category", "category_path", "course_subcategory")
    )


def calculate_gpa(courses: list[dict], context: dict) -> dict:
    excluded = set(context.get("general_elective_codes") or [])
    scales = context.get("grading_scales") or {}
    points = credits = 0.0
    count = 0
    for course in courses:
        code = str(course.get("code") or course.get("course_code") or "")
        if context["mode"] == MODERN and (
            general_elective(course) or code in excluded
            or str(scales.get(code) or course.get("grading_scale") or "").strip() == "两级制"
        ):
            continue
        try:
            credit, gpa = float(course["credit"]), float(course["gpa"])
        except (KeyError, ValueError, TypeError):
            continue
        if not math.isfinite(credit) or not math.isfinite(gpa) or credit <= 0 or gpa < 0:
            continue
        points += credit * gpa
        credits += credit
        count += 1
    return {"average": points / credits if credits else None, "credits": credits, "count": count}


class GpaPolicyService:
    def __init__(self, data_dir, store, registry=None):
        self.root = Path(data_dir) / "gpa_preferences"
        self.store = store
        self.registry = registry
        self._lock = threading.RLock()

    def _payload(self, entry, resource):
        if not entry or not isinstance(entry.payload, dict):
            return {}
        if self.registry:
            spec = self.registry.get(resource)
            if (entry.schema_version != spec.schema_version
                    or entry.revision_algorithm_version != spec.revision_algorithm_version
                    or entry.payload_type != spec.payload_type):
                return {}
        return entry.payload

    def _path(self, account):
        return self.root / (hashlib.sha256(str(account).encode()).hexdigest() + ".json")

    def preference(self, account):
        with self._lock:
            try:
                saved = json.loads(self._path(account).read_text(encoding="utf-8"))
                override = saved.get("mode")
            except (OSError, ValueError, AttributeError):
                override = None
        override = override if override in MODES else None
        return {"mode": override or default_mode(account), "default_mode": default_mode(account), "override": override}

    def save(self, account, mode):
        if mode is not None and mode not in MODES:
            raise ValueError("invalid GPA policy")
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".gpa-", suffix=".tmp", dir=self.root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump({"mode": mode}, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                secure_file(Path(name))
                os.replace(name, self._path(account))
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        return self.context(account)

    def context(self, account, scores=None):
        result = self.preference(account)
        report = self.store.get(CacheKey(account, "academic-report"))
        payload = self._payload(report, "academic-report")
        payload = payload.get("report", payload)
        codes, excluded = set(), set()

        def walk(nodes, inherited=False):
            for node in nodes or []:
                blocked = inherited or "通识选修" in str(node.get("name") or "")
                for course in node.get("courses") or []:
                    code = str(course.get("course_code") or course.get("code") or "")
                    if code:
                        codes.add(code)
                        if blocked or general_elective(course):
                            excluded.add(code)
                walk(node.get("children"), blocked)

        walk(payload.get("categories"))
        if scores is None:
            scores_entry = self.store.get(CacheKey(account, "scores"))
            scores = self._payload(scores_entry, "scores").get("scores") or []
        for course in scores:
            code = str(course.get("code") or "")
            if code:
                codes.add(code)
                if general_elective(course):
                    excluded.add(code)
        scales = {}
        entries = self.store.get_many(
            CacheKey(account, "course-outline-metadata", f"course:{code}") for code in codes
        )
        for code in codes:
            entry = entries.get(CacheKey(account, "course-outline-metadata", f"course:{code}"))
            metadata = self._payload(entry, "course-outline-metadata")
            if metadata.get("grading_scale"):
                scales[code] = metadata["grading_scale"]
        unknown = sum(
            1 for course in scores
            if str(course.get("code") or "") not in excluded
            and not (scales.get(str(course.get("code") or "")) or course.get("grading_scale"))
        )
        return {
            **result, "general_elective_codes": sorted(excluded), "grading_scales": scales,
            "report_available": bool(payload.get("categories")), "missing_grading_scales": unknown,
        }

    def summarize(self, account, courses):
        context = self.context(account, courses)
        return {**calculate_gpa(courses, context), "policy": context}
