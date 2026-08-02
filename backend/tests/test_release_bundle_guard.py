from pathlib import Path

import struct

import pytest
from tools.check_release_bundle import (
    find_forbidden,
    find_structure_violations,
    inspect_bundle,
    main,
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
    internal = root / "_internal"
    internal.mkdir(parents=True)
    target = internal / "libexample.so.1"
    target.write_bytes(b"library")
    link = internal / "libexample.so"
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


def _desktop_bundle(root, *, signed=False):
    internal = root / "_internal"
    (internal / "frontend" / "build").mkdir(parents=True)
    (internal / "VERSION").write_text("1.0.0", encoding="utf-8")
    (internal / "frontend" / "build" / "index.html").write_text(
        '<div id="root"></div>', encoding="utf-8"
    )
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    optional = 0x80 + 24
    struct.pack_into("<H", data, optional, 0x20B)
    if signed:
        security = optional + 112 + 8 * 4
        struct.pack_into("<II", data, security, 0x180, 16)
        struct.pack_into("<I", data, 0x180, 16)
    (root / "NEU-JWXT-Toolkit.exe").write_bytes(data)


def test_desktop_layout_and_explicit_signature_policy(tmp_path):
    unsigned = tmp_path / "unsigned"
    unsigned.mkdir()
    _desktop_bundle(unsigned)
    assert find_structure_violations(unsigned, signature_policy="ignore") == []
    assert find_structure_violations(
        unsigned, signature_policy="require-present"
    ) == ["desktop launcher has no structurally valid Authenticode table"]

    signed = tmp_path / "signed"
    signed.mkdir()
    _desktop_bundle(signed, signed=True)
    assert find_structure_violations(
        signed, signature_policy="require-present"
    ) == []


def test_desktop_layout_rejects_missing_assets_and_extra_launchers(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "NEU-JWXT-Toolkit.exe").write_bytes(b"not-pe")
    (root / "unexpected.exe").write_bytes(b"MZ")
    (root / "run.cmd").write_text("start unexpected.exe", encoding="utf-8")

    violations = find_structure_violations(root)
    assert "desktop launcher is missing or is not a PE executable" in violations
    assert "unexpected top-level executables: unexpected.exe" in violations
    assert "unexpected top-level launcher scripts: run.cmd" in violations
    assert any(item.startswith("missing desktop bundle path:") for item in violations)


def test_installed_desktop_allows_only_expected_inno_uninstaller_files(
    tmp_path, capsys
):
    root = tmp_path / "installed"
    root.mkdir()
    _desktop_bundle(root)
    for name in ("unins000.exe", "unins000.dat", "unins000.msg"):
        (root / name).write_bytes(b"installer-owned")

    assert (
        "unexpected Inno installer files: "
        "unins000.dat, unins000.exe, unins000.msg"
    ) in find_structure_violations(root)
    assert find_structure_violations(root, allow_installer_files=True) == []
    assert main(["--allow-installer-files", str(root)]) == 0
    assert "validation passed" in capsys.readouterr().out

    (root / "unins001.exe").write_bytes(b"not part of the expected fresh install")
    (root / "unins001.dat").write_bytes(b"not part of the expected fresh install")
    (root / "unins000.cmd").write_text("unexpected", encoding="utf-8")
    violations = find_structure_violations(root, allow_installer_files=True)
    assert "unexpected Inno installer files: unins001.dat, unins001.exe" in violations
    assert "unexpected top-level launcher scripts: unins000.cmd" in violations


def test_inspect_bundle_combines_data_and_structure_checks(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    _desktop_bundle(root)
    (root / "credentials.json").write_text("{}", encoding="utf-8")
    violations = inspect_bundle(root)
    assert violations == ["forbidden content: credentials.json"]


def test_pyinstaller_specs_keep_inspectable_onedir_without_upx():
    project = Path(__file__).resolve().parents[2]
    for name, noarchive in (
        ("desktop.spec", "noarchive=True"),
        ("server.spec", "noarchive=False"),
    ):
        text = (project / "packaging" / "pyinstaller" / name).read_text(
            encoding="utf-8"
        )
        assert "hiddenimports=[]" in text
        assert noarchive in text
        assert "upx=False" in text
        assert 'excludes=["pytest", "_pytest"]' in text
        assert "COLLECT(" in text
        assert "collect_submodules" not in text
