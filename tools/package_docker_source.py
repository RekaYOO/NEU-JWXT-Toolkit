"""Package the Go image build context without Git history or runtime state."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
FILES = """go.Dockerfile go.Dockerfile.dockerignore go.mod go.sum VERSION LICENSE
frontend/index.html frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/vite.config.ts
tools/prepare_frontend_assets.mjs tools/check_performance_budget.mjs tools/frontend_performance_budget.json
tools/build_go_release.py packaging/go/compose.yaml packaging/go/licenses/toggle-selection-1.0.6.txt
backend/core/course_selection/THIRD_PARTY_NOTICE.md docs/部署/Go运行包.md docs/部署/Go备份与恢复.md docs/部署/旧版资料导入.md""".split()


def package(output):
    inputs = {ROOT / name for name in FILES}
    for directory, suffixes in (("cmd", {".go"}), ("internal", {".go"}),
                                ("frontend/src", {".js", ".tsx", ".css", ".json"}),
                                ("frontend/public", {".png", ".ico", ".webmanifest"})):
        inputs.update(path for path in (ROOT / directory).rglob("*") if path.suffix in suffixes
                      and not path.name.endswith(("_test.go", ".test.js")))
    for path in inputs:
        if not path.is_file() or path.resolve() != path or any(part.startswith(".") for part in path.relative_to(ROOT).parts):
            raise ValueError(f"Unexpected build input: {path}")
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for path in sorted(inputs):
            archive.write(path, path.relative_to(ROOT).as_posix())
    print(f"Packaged {len(inputs)} files: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    package(parser.parse_args().output)
