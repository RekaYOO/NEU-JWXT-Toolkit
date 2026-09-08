"""Android-only compatibility changes to locked pure-Python wheel archives."""

from __future__ import annotations

import argparse
import ast
import base64
import csv
import hashlib
import io
from pathlib import Path
import zipfile


def uvicorn_initializer(source: str) -> str:
    tree = ast.parse(source)
    matches = [node for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == "uvicorn.main"]
    if len(matches) != 1 or {item.name for item in matches[0].names} != {"Server", "main", "run"}:
        raise ValueError("Locked Uvicorn initializer changed; review the Android adaptation")
    tree.body[tree.body.index(matches[0])] = ast.ImportFrom(
        module="uvicorn.server", names=[ast.alias(name="Server")], level=0,
    )
    tree.body.extend(ast.parse("""
def __getattr__(name):
    if name in {"main", "run"}:
        from importlib import import_module
        return getattr(import_module("uvicorn.main"), name)
    raise AttributeError(name)
""").body)
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


def patch_uvicorn(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        entries = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    if "uvicorn/ANDROID_COMPATIBILITY.txt" in entries:
        return
    target = "uvicorn/__init__.py"
    entries[target] = uvicorn_initializer(entries[target].decode("utf-8")).encode("utf-8")
    record = next(name for name in entries if name.endswith(".dist-info/RECORD"))
    entries["uvicorn/ANDROID_COMPATIBILITY.txt"] = (
        "Android packaging defers Uvicorn CLI imports until requested. Config and Server "
        "retain their upstream implementations; multi-process CLI execution is not supported "
        "on Android. Dependency version and license are unchanged.\n"
    ).encode("utf-8")
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name, content in sorted(entries.items()):
        if name != record:
            digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii")
            writer.writerow([name, f"sha256={digest}", str(len(content))])
    writer.writerow([record, "", ""])
    entries[record] = output.getvalue().encode("utf-8")
    temporary = wheel.with_suffix(".whl.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            entry = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content)
    temporary.replace(wheel)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("wheelhouse", type=Path)
    args = parser.parse_args()
    wheels = list(args.wheelhouse.glob("uvicorn-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Expected exactly one locked Uvicorn wheel")
    patch_uvicorn(wheels[0])
