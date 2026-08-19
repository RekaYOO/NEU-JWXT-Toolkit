import hashlib
import json
import struct
from pathlib import Path

import pytest
from tools.check_release_bundle import (
    find_forbidden,
    find_structure_violations,
    inspect_bundle,
)


def test_release_bundle_guard_rejects_runtime_and_personal_data(tmp_path):
    root = tmp_path / "bundle"
    (root / "app").mkdir(parents=True)
    (root / "app" / "cache.db-wal").write_bytes(b"private")
    (root / "grade_tracking_outbox.json").write_text(
        "{}", encoding="utf-8"
    )
    (root / "data").mkdir()
    (root / "data" / "session.json").write_text("{}", encoding="utf-8")

    found = {str(path).replace("\\", "/") for path in find_forbidden(root)}

    assert "app/cache.db-wal" in found
    assert "grade_tracking_outbox.json" in found
    assert "data" in found
    assert "data/session.json" in found


def test_release_bundle_guard_allows_program_and_examples(tmp_path):
    root = tmp_path / "bundle"
    (root / "app").mkdir(parents=True)
    (root / "examples").mkdir()
    (root / "app" / "neu-jwxt-server").write_bytes(b"binary")
    (root / "examples" / "Caddyfile").write_text(
        "reverse_proxy 127.0.0.1:8000", encoding="utf-8"
    )
    (root / "examples" / "config.example.json").write_text(
        "{}", encoding="utf-8"
    )

    assert find_forbidden(root) == []


def test_release_bundle_guard_rejects_new_private_artifact_types(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    for name in (
        "festival_activities.json",
        "festival-certificates-2026.zip",
        "runtime.json",
        "config.json",
        "account.p12",
        "id_ed25519",
    ):
        (root / name).write_bytes(b"private")

    found = {path.name for path in find_forbidden(root)}
    assert found == {
        "festival_activities.json",
        "festival-certificates-2026.zip",
        "runtime.json",
        "config.json",
        "account.p12",
        "id_ed25519",
    }


def test_release_bundle_guard_allows_internal_relative_symlink(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir(parents=True)
    target = root / "libexample.so.1"
    target.write_bytes(b"library")
    link = root / "libexample.so"
    try:
        link.symlink_to("libexample.so.1")
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    assert find_forbidden(root) == []


def test_release_bundle_guard_rejects_unsafe_symlinks(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    links = {
        "escape": Path("..") / "outside.bin",
        "absolute": outside.resolve(),
        "dangling": Path("missing.bin"),
    }
    try:
        for name, target in links.items():
            (root / name).symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    found = {path.name for path in find_forbidden(root)}
    assert found == set(links)


def _desktop_bundle(root):
    (root / "frontend" / "build").mkdir(parents=True)
    (root / "backend" / "core" / "course_selection").mkdir(parents=True)
    (root / "VERSION").write_text("1.0.0", encoding="utf-8")
    (root / "LICENSE").write_text("MIT License", encoding="utf-8")
    (root / "backend" / "core" / "course_selection" / "THIRD_PARTY_NOTICE.md").write_text(
        "Course_Weight-Optimizer MIT License", encoding="utf-8"
    )
    (root / "frontend" / "build" / "index.html").write_text(
        '<div id="root"></div>', encoding="utf-8"
    )
    for name in (
        "favicon.ico",
        "manifest.webmanifest",
        "icon-192.png",
        "icon-512.png",
        "apple-touch-icon.png",
    ):
        (root / "frontend" / "build" / name).write_bytes(b"brand")
    (root / "app.ico").write_bytes(b"brand")
    (root / "NEU-JWXT-Toolkit.exe").write_bytes(b"MZ")


def test_desktop_layout_accepts_compiled_standalone_runtime(tmp_path):
    root = tmp_path / "desktop"
    root.mkdir()
    _desktop_bundle(root)
    assert find_structure_violations(root) == []


def test_desktop_layout_rejects_missing_assets_and_extra_launchers(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    _desktop_bundle(root)
    (root / "unexpected.exe").write_bytes(b"MZ")
    (root / "run.ps1").write_text("start unexpected.exe", encoding="utf-8")
    (root / "frontend" / "build" / "index.html").unlink()

    violations = find_structure_violations(root)
    assert "unexpected top-level executables: unexpected.exe" in violations
    assert "unexpected top-level launcher scripts: run.ps1" in violations
    assert any(item.startswith("missing desktop bundle path:") for item in violations)


def test_portable_desktop_rejects_installer_owned_files(tmp_path):
    root = tmp_path / "portable"
    root.mkdir()
    _desktop_bundle(root)
    (root / "unins000.exe").write_bytes(b"installer-owned")

    assert "unexpected top-level executables: unins000.exe" in find_structure_violations(root)


def _server_bundle(root):
    (root / "frontend" / "build").mkdir(parents=True)
    (root / "backend" / "core" / "course_selection").mkdir(parents=True)
    (root / "VERSION").write_text("1.0.0", encoding="utf-8")
    (root / "LICENSE").write_text("MIT License", encoding="utf-8")
    (root / "backend" / "core" / "course_selection" / "THIRD_PARTY_NOTICE.md").write_text(
        "Course_Weight-Optimizer MIT License", encoding="utf-8"
    )
    (root / "frontend" / "build" / "index.html").write_text(
        '<div id="root"></div>', encoding="utf-8"
    )
    for name in (
        "favicon.ico",
        "manifest.webmanifest",
        "icon-192.png",
        "icon-512.png",
        "apple-touch-icon.png",
    ):
        (root / "frontend" / "build" / name).write_bytes(b"brand")
    (root / "neu-jwxt-server").write_bytes(b"\x7fELF")


def test_server_layout_accepts_nuitka_standalone_and_assembled_package(tmp_path):
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    _server_bundle(standalone)
    assert find_structure_violations(standalone) == []

    package = tmp_path / "package"
    (package / "app").mkdir(parents=True)
    _server_bundle(package / "app")
    assert find_structure_violations(package) == []


def test_inspect_bundle_combines_data_and_structure_checks(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    _desktop_bundle(root)
    (root / "credentials.json").write_text("{}", encoding="utf-8")
    violations = inspect_bundle(root)
    assert violations == ["forbidden content: credentials.json"]


def test_nuitka_build_keeps_inspectable_standalone_payload_and_windows_metadata():
    project = Path(__file__).resolve().parents[2]
    text = (project / "packaging" / "nuitka" / "build.py").read_text(encoding="utf-8")
    assert '"--mode=standalone"' in text
    assert "--mode=onefile" not in text
    assert "--include-data-dir=" in text
    assert "--include-data-files=" in text
    assert "THIRD_PARTY_NOTICE.md" in text
    assert "PROJECT_ROOT / 'LICENSE'" in text
    assert '"--include-package=uvicorn"' in text
    assert '"--include-package-data=certifi"' in text
    assert '"--nofollow-import-to=pytest"' in text
    assert '"--nofollow-import-to=_pytest"' in text
    assert '"desktop"' in text
    assert "NEU-JWXT-Toolkit.exe" in text
    assert "--windows-console-mode=disable" in text
    assert "--company-name=NEU-JWXT-Toolkit Contributors" in text
    assert "--product-name=NEU JWXT Toolkit" in text
    assert "--file-version=" in text
    assert "--product-version=" in text


def test_branding_assets_cover_web_and_windows_consumers():
    project = Path(__file__).resolve().parents[2]
    public = project / "frontend" / "public"
    manifest = json.loads((public / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert {icon["sizes"] for icon in manifest["icons"]} == {"192x192", "512x512"}
    assert manifest["lang"] == "zh-CN"

    for name, expected_size in (
        ("icon-192.png", (192, 192)),
        ("icon-512.png", (512, 512)),
        ("apple-touch-icon.png", (180, 180)),
    ):
        payload = (public / name).read_bytes()
        assert payload[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", payload[16:24]) == expected_size

    ico = (project / "packaging" / "windows" / "app.ico").read_bytes()
    reserved, image_type, count = struct.unpack("<HHH", ico[:6])
    assert (reserved, image_type) == (0, 1)
    sizes = {
        256 if ico[6 + index * 16] == 0 else ico[6 + index * 16]
        for index in range(count)
    }
    assert sizes == {16, 20, 24, 32, 40, 48, 64, 128, 256}
    assert (public / "favicon.ico").read_bytes() == ico

    master = (project / "assets" / "branding" / "app-icon.png").read_bytes()
    assert master[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", master[16:24]) == (1254, 1254)
    assert master[25] == 6  # RGBA

    official = project / "assets" / "branding" / "neu-official-emblem.png"
    assert hashlib.sha256(official.read_bytes()).hexdigest() == (
        "c11f01b8bd18c6586bd5c53fb40ff87a6ed243ef95b3e7c7cdb898700d1406d6"
    )

    html = (public / "index.html").read_text(encoding="utf-8")
    assert "favicon.ico" in html
    assert "manifest.webmanifest" in html
    assert "apple-touch-icon.png" in html
