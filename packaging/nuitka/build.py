"""Build reproducible standalone release payloads with Nuitka."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WINDOWS_ICON = PROJECT_ROOT / "packaging" / "windows" / "app.ico"


@dataclass(frozen=True)
class BuildTarget:
    entrypoint: Path
    output_name: str
    payload_name: str
    host_platform: str


TARGETS = {
    "desktop": BuildTarget(
        entrypoint=PROJECT_ROOT / "launchers" / "desktop.py",
        output_name="NEU-JWXT-Toolkit.exe",
        payload_name="NEU-JWXT-Toolkit",
        host_platform="win32",
    ),
    "server": BuildTarget(
        entrypoint=PROJECT_ROOT / "launchers" / "server.py",
        output_name="neu-jwxt-server",
        payload_name="neu-jwxt-server",
        host_platform="linux",
    ),
}


def _contained_path(path: Path, parent: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(parent.resolve())
    return resolved


def _clean_tree(path: Path, parent: Path) -> None:
    target = _contained_path(path, parent)
    if target == parent.resolve():
        raise ValueError("refusing to remove the output parent itself")
    if target.exists():
        shutil.rmtree(target)


def build_command(target_name: str, work_dir: Path) -> list[str]:
    target = TARGETS[target_name]
    command = [
        sys.executable,
        "-m",
        "nuitka",
        "--mode=standalone",
        "--assume-yes-for-downloads",
        "--remove-output",
        f"--output-dir={work_dir}",
        f"--output-filename={target.output_name}",
        f"--include-data-dir={PROJECT_ROOT / 'frontend' / 'build'}=frontend/build",
        f"--include-data-files={PROJECT_ROOT / 'VERSION'}=VERSION",
        "--include-package=uvicorn",
        "--include-package-data=certifi",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=_pytest",
        f"--report={work_dir / 'compilation-report.xml'}",
    ]
    if target_name == "desktop":
        command.extend(
            (
                "--msvc=latest",
                "--windows-console-mode=disable",
                f"--windows-icon-from-ico={WINDOWS_ICON}",
                f"--include-data-files={WINDOWS_ICON}=app.ico",
            )
        )
    command.append(str(target.entrypoint))
    return command


def build(target_name: str) -> Path:
    target = TARGETS[target_name]
    if not sys.platform.startswith(target.host_platform):
        raise RuntimeError(
            f"{target_name} payload must be built on {target.host_platform}, not {sys.platform}"
        )
    frontend_index = PROJECT_ROOT / "frontend" / "build" / "index.html"
    if not frontend_index.is_file():
        raise FileNotFoundError(
            "frontend/build/index.html is missing; run the frontend production build first"
        )
    if target_name == "desktop" and not WINDOWS_ICON.is_file():
        raise FileNotFoundError(f"Windows application icon is missing: {WINDOWS_ICON}")

    build_parent = PROJECT_ROOT / "build" / "nuitka"
    work_dir = build_parent / target_name
    final_parent = PROJECT_ROOT / "dist"
    final_dir = final_parent / target.payload_name
    work_parent = work_dir.parent
    work_parent.mkdir(parents=True, exist_ok=True)
    final_parent.mkdir(parents=True, exist_ok=True)
    _clean_tree(work_dir, build_parent)
    _clean_tree(final_dir, final_parent)
    work_dir.mkdir(parents=True)

    subprocess.run(
        build_command(target_name, work_dir),
        cwd=PROJECT_ROOT,
        check=True,
    )
    generated = work_dir / f"{target.entrypoint.stem}.dist"
    executable = generated / target.output_name
    if not executable.is_file():
        raise FileNotFoundError(f"Nuitka output is incomplete: {executable}")
    shutil.copytree(generated, final_dir, symlinks=True)
    return final_dir


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=tuple(TARGETS))
    args = parser.parse_args(arguments)
    output = build(args.target)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
