"""Validate release layout, sensitive-data exclusions and signing policy."""

from __future__ import annotations

import argparse
import os
import re
import struct
import sys
from pathlib import Path


FORBIDDEN_FILE_PATTERNS = (
    re.compile(r"^cache\.db(?:-(?:wal|shm))?$", re.IGNORECASE),
    re.compile(r"^(?:session|cookies?|credentials)(?:[._-].*)?\.json$", re.IGNORECASE),
    re.compile(r"^scores.*\.csv$", re.IGNORECASE),
    re.compile(r"^academic_report(?:[._-].*)?\.json$", re.IGNORECASE),
    re.compile(r"^research_(?:training_cache|favorites)(?:[._-].*)?\.json$", re.IGNORECASE),
    re.compile(r"^festival[_-]?activities(?:[._-].*)?\.json$", re.IGNORECASE),
    re.compile(r"^(?:festival-certificates|四节活动证书_).+\.zip$", re.IGNORECASE),
    re.compile(r"^(?:runtime|config)\.json$", re.IGNORECASE),
    re.compile(r"^avatar(?:_meta)?(?:\.(?:png|jpe?g|webp|json))$", re.IGNORECASE),
    re.compile(r"^grade_tracking_(?:snapshot|state|outbox|config)(?:[._-].*)?\.json$", re.IGNORECASE),
    re.compile(r"^\.env(?:\..*)?$", re.IGNORECASE),
    re.compile(r".*\.(?:pfx|p12)$", re.IGNORECASE),
    re.compile(r"^(?:id_rsa|id_ed25519|private[_-]?key)(?:\..*)?$", re.IGNORECASE),
    re.compile(r".*\.log$", re.IGNORECASE),
)
FORBIDDEN_TOP_LEVEL_DIRECTORIES = {"data", "logs", "成绩"}
FORBIDDEN_ANYWHERE_DIRECTORIES = {"gpa_simulations"}
SCRIPT_SUFFIXES = {".bat", ".cmd", ".ps1", ".vbs", ".wsf"}
INNO_INSTALLER_FILES = {"unins000.exe", "unins000.dat", "unins000.msg"}
INNO_INSTALLER_PATTERN = re.compile(
    r"^unins\d{3}\.(?:exe|dat|msg)$", re.IGNORECASE
)


def _unsafe_symlink(path: Path, root: Path) -> bool:
    """Reject links that are broken, absolute, cyclic, or escape the bundle."""
    try:
        raw_target = Path(os.readlink(path))
        if raw_target.is_absolute() or not path.exists():
            return True
        resolved_root = root.resolve()
        resolved_target = path.resolve(strict=True)
        resolved_target.relative_to(resolved_root)
        return False
    except (OSError, RuntimeError, ValueError):
        return True


def find_forbidden(root: Path) -> list[Path]:
    """Return personal/runtime files that must never enter a release."""
    found: list[Path] = []
    if not root.exists():
        raise FileNotFoundError(root)
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        parts_lower = tuple(part.lower() for part in relative.parts)
        if path.is_symlink():
            # PyInstaller 6 uses relative symlinks extensively on POSIX. They
            # are safe when they resolve to an existing target in this bundle;
            # `cp -a` and `tar` preserve them without dereferencing.
            if _unsafe_symlink(path, root):
                found.append(relative)
            continue
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


def _pe_signature_table(path: Path) -> tuple[int, int] | None:
    """Return the PE Authenticode certificate table range, if structurally present.

    This intentionally checks presence, not publisher trust or revocation.
    Trust validation belongs to the signing environment (`signtool verify /pa`).
    """
    try:
        data = path.read_bytes()
        if len(data) < 0x40 or data[:2] != b"MZ":
            return None
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if pe_offset + 24 > len(data) or data[pe_offset:pe_offset + 4] != b"PE\0\0":
            return None
        optional = pe_offset + 24
        magic = struct.unpack_from("<H", data, optional)[0]
        directory = optional + (112 if magic == 0x20B else 96 if magic == 0x10B else -1)
        if directory < optional:
            return None
        security_entry = directory + 8 * 4
        if security_entry + 8 > len(data):
            return None
        offset, size = struct.unpack_from("<II", data, security_entry)
        if offset == 0 or size < 8 or offset + size > len(data):
            return None
        certificate_size = struct.unpack_from("<I", data, offset)[0]
        if certificate_size < 8 or certificate_size > size:
            return None
        return offset, size
    except (OSError, struct.error, OverflowError):
        return None


def _desktop_layout_violations(
    root: Path, signature_policy: str, allow_installer_files: bool
) -> list[str]:
    violations: list[str] = []
    launcher = root / "NEU-JWXT-Toolkit.exe"
    internal = root / "_internal"
    required = (
        internal,
        internal / "VERSION",
        internal / "frontend" / "build" / "index.html",
    )
    for path in required:
        if not path.exists():
            violations.append(f"missing desktop bundle path: {path.relative_to(root)}")
    if not launcher.is_file() or launcher.read_bytes()[:2] != b"MZ":
        violations.append("desktop launcher is missing or is not a PE executable")
    installer_files = sorted(
        path.name
        for path in root.iterdir()
        if path.is_file() and INNO_INSTALLER_PATTERN.fullmatch(path.name)
    )
    allowed_files = INNO_INSTALLER_FILES if allow_installer_files else set()
    unexpected_installer_files = [
        name for name in installer_files if name.lower() not in allowed_files
    ]
    if unexpected_installer_files:
        violations.append(
            "unexpected Inno installer files: "
            + ", ".join(unexpected_installer_files)
        )
    extra_executables = sorted(
        path.name
        for path in root.glob("*.exe")
        if path.name != launcher.name
        and not INNO_INSTALLER_PATTERN.fullmatch(path.name)
    )
    if extra_executables:
        violations.append("unexpected top-level executables: " + ", ".join(extra_executables))
    scripts = sorted(
        path.name for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in SCRIPT_SUFFIXES
    )
    if scripts:
        violations.append("unexpected top-level launcher scripts: " + ", ".join(scripts))
    if signature_policy == "require-present" and _pe_signature_table(launcher) is None:
        violations.append("desktop launcher has no structurally valid Authenticode table")
    return violations


def find_structure_violations(
    root: Path,
    *,
    signature_policy: str = "ignore",
    allow_installer_files: bool = False,
) -> list[str]:
    """Validate a recognized frozen bundle without rejecting package wrappers."""
    if signature_policy not in {"ignore", "require-present"}:
        raise ValueError(f"unknown signature policy: {signature_policy}")
    if (root / "NEU-JWXT-Toolkit.exe").exists():
        return _desktop_layout_violations(
            root, signature_policy, allow_installer_files
        )
    # Linux frozen root or assembled package. Server signing is handled by the
    # platform package/repository rather than Authenticode.
    server = root / "neu-jwxt-server"
    packaged_server = root / "app" / "neu-jwxt-server"
    if server.exists() and not (root / "_internal").is_dir():
        return ["frozen server bundle is missing _internal"]
    if (root / "app").is_dir() and not packaged_server.is_file():
        return ["assembled server package is missing app/neu-jwxt-server"]
    return []


def inspect_bundle(
    root: Path,
    *,
    signature_policy: str = "ignore",
    allow_installer_files: bool = False,
) -> list[str]:
    violations = [f"forbidden content: {path}" for path in find_forbidden(root)]
    violations.extend(
        find_structure_violations(
            root,
            signature_policy=signature_policy,
            allow_installer_files=allow_installer_files,
        )
    )
    return violations


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Check frozen release bundles for private data and unsafe layout."
    )
    parser.add_argument(
        "--signature-policy",
        choices=("ignore", "require-present"),
        default="ignore",
        help=(
            "ignore for local/unsigned builds; require-present after the release "
            "signing step (presence only, then run signtool verify /pa)"
        ),
    )
    parser.add_argument(
        "--allow-installer-files",
        action="store_true",
        help=(
            "allow only Inno Setup's expected unins000.exe/.dat/.msg files "
            "in an installed desktop bundle; portable bundles stay strict"
        ),
    )
    parser.add_argument("roots", nargs="+")
    args = parser.parse_args(arguments)
    violations: list[str] = []
    for value in args.roots:
        root = Path(value).resolve()
        try:
            for item in inspect_bundle(
                root,
                signature_policy=args.signature_policy,
                allow_installer_files=args.allow_installer_files,
            ):
                violations.append(f"{root}: {item}")
        except FileNotFoundError:
            violations.append(f"{root}: bundle root does not exist")
    if violations:
        print("Release bundle validation failed:", file=sys.stderr)
        print("\n".join(f"- {item}" for item in violations), file=sys.stderr)
        return 1
    print(
        "Release bundle validation passed "
        f"(signature policy: {args.signature_policy})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
