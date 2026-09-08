#!/usr/bin/env python3
"""Fail a release when Android package identity or contents drift."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import zipfile
import io
import struct
from pathlib import Path


def _tool(name: str) -> Path:
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or "")
    filenames = {name, f"{name}.exe", f"{name}.bat"}
    candidates = sorted(
        (path for path in (sdk / "build-tools").glob("*/*") if path.name in filenames),
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"Android build tool not found: {name}")
    return candidates[0]


def _run(*args: str | Path) -> str:
    return subprocess.run(
        [str(item) for item in args], check=True, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    ).stdout


def _badging(apk: Path) -> str:
    return _run(_tool("aapt"), "dump", "badging", apk)


def _certificate(apk: Path) -> str:
    output = _run(_tool("apksigner"), "verify", "--verbose", "--print-certs", apk)
    match = re.search(r"certificate SHA-256 digest:\s*([0-9a-f]+)", output, re.I)
    if not match:
        raise SystemExit(f"Unable to read signing certificate from {apk.name}")
    return match.group(1).lower()


def _check_elf(content: bytes, name: str, abi: str) -> None:
    if len(content) < 64 or content[:6] != b"\x7fELF\x02\x01":
        raise SystemExit(f"Native library is not a little-endian ELF64: {name}")
    machine = struct.unpack_from("<H", content, 18)[0]
    if machine != {"arm64-v8a": 183, "x86_64": 62}[abi]:
        raise SystemExit(f"Native library architecture mismatch: {name}")
    offset = struct.unpack_from("<Q", content, 32)[0]
    size, count = struct.unpack_from("<HH", content, 54)
    if size < 56 or count == 0 or offset + size * count > len(content):
        raise SystemExit(f"Invalid native program headers: {name}")
    loads = 0
    for index in range(count):
        position = offset + index * size
        if struct.unpack_from("<I", content, position)[0] != 1:
            continue
        loads += 1
        file_offset, address = struct.unpack_from("<QQ", content, position + 8)
        alignment = struct.unpack_from("<Q", content, position + 48)[0]
        if alignment < 16384 or alignment & (alignment - 1) or (address - file_offset) % alignment:
            raise SystemExit(f"Native library is not 16 KiB page compatible: {name}")
    if not loads:
        raise SystemExit(f"Native library has no loadable segment: {name}")


def _check_entries(
    archive: zipfile.ZipFile, prefix: str = "", depth: int = 0, abi: str | None = None,
) -> None:
    for entry in archive.infolist():
        name = entry.filename.lower()
        parts = set(name.split("/"))
        basename = name.rsplit("/", 1)[-1]
        public_ca = name in {"certifi/cacert.pem", "assets/chaquopy/cacert.pem"}
        if public_ca:
            content = archive.read(entry)
            if (b"PRIVATE KEY" in content or b"BEGIN CERTIFICATE" not in content
                or re.search(rb"-----BEGIN (?!CERTIFICATE-----)", content)):
                raise SystemExit(f"Invalid public trust bundle: {prefix}{entry.filename}")
        if (name.endswith((".map", ".jks", ".keystore", ".key"))
            or (name.endswith(".pem") and not public_ca)
            or ".git" in parts or "__pycache__" in parts or ".env" in parts
            or basename in {"runtime.json", "config.json", "mobile_notification_outbox.json"}
            or "backend/tests/" in name):
            raise SystemExit(f"Sensitive or development entry: {prefix}{entry.filename}")
        if abi and name.endswith(".so"):
            _check_elf(archive.read(entry), f"{prefix}{entry.filename}", abi)
        if depth < 2 and name.endswith((".zip", ".imy")):
            if entry.file_size > 512 * 1024 * 1024:
                raise SystemExit(f"Nested payload exceeds inspection limit: {prefix}{entry.filename}")
            content = io.BytesIO(archive.read(entry))
            if zipfile.is_zipfile(content):
                with zipfile.ZipFile(content) as nested:
                    _check_entries(nested, f"{prefix}{entry.filename}!/", depth + 1, abi)


def _check_android_python_patch(archive: zipfile.ZipFile) -> None:
    payload = "assets/chaquopy/requirements-common.imy"
    if payload not in archive.namelist():
        raise SystemExit("Local APK is missing its shared Python requirements")
    with zipfile.ZipFile(io.BytesIO(archive.read(payload))) as requirements:
        marker = "uvicorn/ANDROID_COMPATIBILITY.txt"
        if marker not in requirements.namelist():
            raise SystemExit("Local APK contains Uvicorn without the Android compatibility patch")


def verify(apk: Path, package: str, version: str, version_code: str, abi: str, local: bool) -> str:
    badging = _badging(apk)
    expected = f"package: name='{package}' versionCode='{version_code}' versionName='{version}'"
    if expected not in badging:
        raise SystemExit(f"Package metadata mismatch for {apk.name}: expected {expected}")
    if "sdkVersion:'24'" not in badging:
        raise SystemExit(f"Minimum SDK is not API 24 in {apk.name}")
    if "application-debuggable" in badging:
        raise SystemExit(f"Release APK is debuggable: {apk.name}")

    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        _check_entries(archive, abi=abi)
        if local:
            _check_android_python_patch(archive)
    lowered = [name.lower() for name in names]
    if not any(name.endswith("assets/index.html") for name in names):
        raise SystemExit(f"Embedded frontend is missing from {apk.name}")
    for license_name in ("assets/licenses/LICENSE", "assets/licenses/THIRD_PARTY_NOTICE.md"):
        if license_name not in names:
            raise SystemExit(f"Required license is missing from {apk.name}: {license_name}")
    native_abis = {parts[1] for name in names if name.startswith("lib/") for parts in [name.split("/")] if len(parts) > 2}
    if native_abis and native_abis != {abi}:
        raise SystemExit(f"Unexpected ABIs in {apk.name}: {sorted(native_abis)}")
    has_python = any("chaquopy" in name or "libpython3.13" in name for name in lowered)
    if local and (not has_python or abi not in native_abis):
        raise SystemExit(f"Local APK does not contain the Python runtime for {abi}")
    if not local and has_python:
        raise SystemExit("Client APK unexpectedly contains the Python runtime")
    _run(_tool("zipalign"), "-c", "-P", "16", "4", apk)
    return _certificate(apk)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("client", type=Path)
    parser.add_argument("local", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--version-code", required=True)
    parser.add_argument("--abi", default="arm64-v8a")
    args = parser.parse_args()
    client_cert = verify(
        args.client, "io.github.rekayoo.neujwxt.client", args.version,
        args.version_code, args.abi, False,
    )
    local_cert = verify(
        args.local, "io.github.rekayoo.neujwxt.local", args.version,
        args.version_code, args.abi, True,
    )
    if client_cert != local_cert:
        raise SystemExit("Client and local APKs are not signed with the same certificate")
    print(f"Verified Android APKs; signing certificate SHA-256: {client_cert}")


if __name__ == "__main__":
    main()
