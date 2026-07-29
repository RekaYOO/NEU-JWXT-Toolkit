"""Fail a release build when runtime or personal data entered the bundle."""

from __future__ import annotations

import re
import sys
from pathlib import Path


FORBIDDEN_FILE_PATTERNS = (
    re.compile(r"^cache\.db(?:-(?:wal|shm))?$", re.IGNORECASE),
    re.compile(r"^(?:session|cookies?|credentials)(?:[._-].*)?\.json$", re.IGNORECASE),
    re.compile(r"^scores.*\.csv$", re.IGNORECASE),
    re.compile(r"^academic_report(?:[._-].*)?\.json$", re.IGNORECASE),
    re.compile(r"^research_(?:training_cache|favorites)(?:[._-].*)?\.json$", re.IGNORECASE),
    re.compile(r"^avatar(?:_meta)?(?:\.(?:png|jpe?g|webp|json))$", re.IGNORECASE),
    re.compile(
        r"^grade_tracking_(?:snapshot|state|outbox|config)(?:[._-].*)?\.json$",
        re.IGNORECASE,
    ),
    re.compile(r"^\.env(?:\..*)?$", re.IGNORECASE),
    re.compile(r".*\.log$", re.IGNORECASE),
)
FORBIDDEN_TOP_LEVEL_DIRECTORIES = {"data", "logs", "成绩"}
FORBIDDEN_ANYWHERE_DIRECTORIES = {"gpa_simulations"}


def find_forbidden(root: Path) -> list[Path]:
    found: list[Path] = []
    if not root.exists():
        raise FileNotFoundError(root)
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        parts_lower = tuple(part.lower() for part in relative.parts)
        if path.is_dir():
            if (
                (len(parts_lower) == 1 and parts_lower[0] in FORBIDDEN_TOP_LEVEL_DIRECTORIES)
                or any(part in FORBIDDEN_ANYWHERE_DIRECTORIES for part in parts_lower)
            ):
                found.append(relative)
            continue
        if (
            (parts_lower and parts_lower[0] in FORBIDDEN_TOP_LEVEL_DIRECTORIES)
            or any(part in FORBIDDEN_ANYWHERE_DIRECTORIES for part in parts_lower[:-1])
            or any(pattern.fullmatch(path.name) for pattern in FORBIDDEN_FILE_PATTERNS)
        ):
            found.append(relative)
    return sorted(set(found), key=lambda item: str(item).lower())


def main(arguments: list[str]) -> int:
    if not arguments:
        print("usage: check_release_bundle.py <bundle-root> [...]", file=sys.stderr)
        return 2
    violations: list[str] = []
    for value in arguments:
        root = Path(value).resolve()
        for relative in find_forbidden(root):
            violations.append(f"{root}: {relative}")
    if violations:
        print("Release bundle contains forbidden runtime/personal data:", file=sys.stderr)
        print("\n".join(f"- {item}" for item in violations), file=sys.stderr)
        return 1
    print("Release bundle data-safety check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

