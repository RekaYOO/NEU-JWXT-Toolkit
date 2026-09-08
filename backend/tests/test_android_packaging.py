from __future__ import annotations

import hashlib
import re
import io
import zipfile
import json
import pytest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANDROID = ROOT / "packaging" / "android"


def _requirements(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        name, version = value.split("==", 1)
        result[name.lower().replace("_", "-")] = version
    return result


def test_android_dependencies_match_backend_runtime_lock():
    backend = _requirements(ROOT / "requirements.lock")
    android = _requirements(ANDROID / "requirements-android.txt")

    assert android == backend
    assert android["pydantic-core"] == "2.46.4"
    assert android["lxml"] == "6.1.1"
    assert android["pycryptodome"] == "3.23.0"


def test_android_modules_keep_python_out_of_client_package():
    client = (ANDROID / "client-app" / "build.gradle").read_text(encoding="utf-8")
    local = (ANDROID / "local-app" / "build.gradle").read_text(encoding="utf-8")

    assert 'applicationId "io.github.rekayoo.neujwxt.client"' in client
    assert 'id "com.chaquo.python"' not in client
    assert 'applicationId "io.github.rekayoo.neujwxt.local"' in local
    assert 'id "com.chaquo.python"' in local
    assert 'version = "3.13"' in local
    assert 'extractPackages("Crypto")' in local


def test_android_version_code_and_gradle_wrapper_are_pinned():
    version_code = (ROOT / "ANDROID_VERSION_CODE").read_text(encoding="ascii").strip()
    assert re.fullmatch(r"[1-9][0-9]*", version_code)
    assert int(version_code) <= 2_100_000_000

    properties = (ANDROID / "gradle" / "wrapper" / "gradle-wrapper.properties").read_text(
        encoding="utf-8"
    )
    assert "gradle-8.11.1-bin.zip" in properties
    assert "distributionSha256Sum=f397b287023acdba1e9f6fc5ea72d22dd63669d59ed4a289a29b1a76eee151c6" in properties
    wrapper = ANDROID / "gradle" / "wrapper" / "gradle-wrapper.jar"
    assert hashlib.sha256(wrapper.read_bytes()).hexdigest() == (
        "2db75c40782f5e8ba1fc278a5574bab070adccb2d21ca5a6e5ed840888448046"
    )


def test_android_release_resolves_tink_annotations_without_disabling_r8():
    shared = (ANDROID / "shared" / "build.gradle").read_text(encoding="utf-8")
    assert 'implementation "com.google.errorprone:error_prone_annotations:2.18.0"' in shared
    for module in ("client-app", "local-app"):
        build = (ANDROID / module / "build.gradle").read_text(encoding="utf-8")
        rules = (ANDROID / module / "proguard-rules.pro").read_text(encoding="utf-8")
        assert "minifyEnabled true" in build
        assert "-ignorewarnings" not in rules
        assert "-dontwarn com.google.errorprone" not in rules


def test_android_manifests_do_not_request_external_storage():
    manifests = [
        ANDROID / "client-app" / "src" / "main" / "AndroidManifest.xml",
        ANDROID / "local-app" / "src" / "main" / "AndroidManifest.xml",
    ]
    for manifest in manifests:
        text = manifest.read_text(encoding="utf-8")
        assert "READ_EXTERNAL_STORAGE" not in text
        assert "WRITE_EXTERNAL_STORAGE" not in text
        assert 'android:allowBackup="false"' in text


def test_android_native_sources_match_pinned_runtime_versions():
    sources = json.loads((ANDROID / "recipes" / "sources.json").read_text())
    pinned = _requirements(ROOT / "requirements.lock")
    assert len(sources) == 3
    for source in sources:
        name, version = source["filename"].removesuffix(".tar.gz").rsplit("-", 1)
        assert pinned[name.replace("_", "-")] == version
        assert re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
        assert source["url"].startswith("https://files.pythonhosted.org/")


def test_android_verifier_inspects_nested_python_payload_and_allows_trust_store():
    from tools.verify_android_release import _check_entries

    with io.BytesIO() as valid:
        with zipfile.ZipFile(valid, "w") as archive:
            archive.writestr("certifi/cacert.pem", "-----BEGIN CERTIFICATE-----\npublic CA\n-----END CERTIFICATE-----")
            archive.writestr("assets/chaquopy/cacert.pem", "-----BEGIN CERTIFICATE-----\npublic CA\n-----END CERTIFICATE-----")
            archive.writestr("assets/static/main.js", "")
        valid.seek(0)
        with zipfile.ZipFile(valid) as archive:
            _check_entries(archive)
    with io.BytesIO() as nested, io.BytesIO() as outer:
        with zipfile.ZipFile(nested, "w") as archive:
            archive.writestr("data/runtime.json", "{}")
        with zipfile.ZipFile(outer, "w") as archive:
            archive.writestr("assets/chaquopy/app.imy", nested.getvalue())
        outer.seek(0)
        with zipfile.ZipFile(outer) as archive, pytest.raises(SystemExit, match="runtime.json"):
            _check_entries(archive)


def test_android_verifier_rejects_private_keys_in_allowlisted_trust_bundles():
    from tools.verify_android_release import _check_entries

    with io.BytesIO() as content:
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("assets/chaquopy/cacert.pem",
                            "-----BEGIN CERTIFICATE-----\n-----BEGIN PRIVATE KEY-----")
        content.seek(0)
        with zipfile.ZipFile(content) as archive, pytest.raises(SystemExit, match="trust bundle"):
            _check_entries(archive)


def test_android_build_tool_lookup_never_selects_aapt2(tmp_path, monkeypatch):
    from tools.verify_android_release import _tool

    folder = tmp_path / "build-tools" / "35.0.0"
    folder.mkdir(parents=True)
    (folder / "aapt").touch()
    (folder / "aapt2").touch()
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path))
    assert _tool("aapt").name == "aapt"


def test_android_tool_output_uses_utf8_independently_of_host_locale():
    import sys
    from tools.verify_android_release import _run

    assert _run(sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes.fromhex('e5b7a5e585b7'))") == "工具"


def test_android_verifier_checks_native_abi_and_page_alignment():
    import struct
    from tools.verify_android_release import _check_elf

    elf = bytearray(120)
    elf[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", elf, 18, 183)
    struct.pack_into("<Q", elf, 32, 64)
    struct.pack_into("<HH", elf, 54, 56, 1)
    struct.pack_into("<I", elf, 64, 1)
    struct.pack_into("<Q", elf, 112, 16384)
    _check_elf(bytes(elf), "module.so", "arm64-v8a")
    with pytest.raises(SystemExit, match="architecture"):
        _check_elf(bytes(elf), "module.so", "x86_64")
    struct.pack_into("<Q", elf, 112, 4096)
    with pytest.raises(SystemExit, match="16 KiB"):
        _check_elf(bytes(elf), "module.so", "arm64-v8a")
    with pytest.raises(SystemExit, match="ELF64"):
        _check_elf(b"invalid", "module.so", "arm64-v8a")
