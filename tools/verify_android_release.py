#!/usr/bin/env python3
"""Fail a release when Android package identity or contents drift."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import zipfile
import io
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
        [str(item) for item in args], check=True, text=True,
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


def _check_entries(archive: zipfile.ZipFile, prefix: str = "", depth: int = 0) -> None:
    for entry in archive.infolist():
        name = entry.filename.lower()
        parts = set(name.split("/"))
        basename = name.rsplit("/", 1)[-1]
        if (name.endswith((".map", ".jks", ".keystore", ".key"))
            or (name.endswith(".pem") and not name.endswith("certifi/cacert.pem"))
            or ".git" in parts or "__pycache__" in parts or ".env" in parts
            or basename in {"runtime.json", "config.json", "mobile_notification_outbox.json"}
            or "backend/tests/" in name):
            raise SystemExit(f"Sensitive or development entry: {prefix}{entry.filename}")
        if depth < 2 and name.endswith((".zip", ".imy")):
            if entry.file_size > 512 * 1024 * 1024:
                raise SystemExit(f"Nested payload exceeds inspection limit: {prefix}{entry.filename}")
            content = io.BytesIO(archive.read(entry))
            if zipfile.is_zipfile(content):
                with zipfile.ZipFile(content) as nested:
                    _check_entries(nested, f"{prefix}{entry.filename}!/", depth + 1)


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
        _check_entries(archive)
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
