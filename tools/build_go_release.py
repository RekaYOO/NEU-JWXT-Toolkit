"""Build version-matched Go/Web bundles without copying user data."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def run(arguments, *, cwd=ROOT, env=None, capture=False):
    return subprocess.run(arguments, cwd=cwd, env=env, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def copy_licenses(destination, go):
    notices = destination / "third-party"
    notices.mkdir()
    modules = set()
    for platform in ("windows", "linux"):
        environment = {**os.environ, "GOOS": platform, "GOARCH": "amd64", "CGO_ENABLED": "0"}
        listing = run([go, "list", "-deps", "-f", "{{if .Module}}{{.Module.Path}}|{{.Module.Version}}|{{.Module.Dir}}{{end}}", "./cmd/toolbox"], env=environment, capture=True)
        modules.update(line for line in listing.splitlines() if line)
    dependencies = []
    for line in sorted(modules):
        name, version, directory = line.split("|", 2)
        if version:
            dependencies.append(("go", name, version, Path(directory)))
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    for location, metadata in lock["packages"].items():
        if not location or metadata.get("dev") or metadata.get("devOptional"):
            continue
        directory = ROOT / "frontend" / location
        if not directory.is_dir():
            if metadata.get("optional"):
                continue
            raise ValueError(f"Missing production package: {location}")
        package = json.loads((directory / "package.json").read_text(encoding="utf-8"))
        dependencies.append(("npm", package["name"], package["version"], directory))
    inventory = []
    for ecosystem, name, version, directory in dependencies:
        identifier = re.sub(r"[^a-zA-Z0-9._-]", "_", f"{ecosystem}-{name}-{version}")
        target = notices / identifier
        target.mkdir(exist_ok=True)
        files = [path for path in directory.iterdir() if path.is_file() and
                 path.name.lower().startswith(("license", "licence", "copying", "notice", "copyright"))]
        provenance = "package license files"
        if not files and ecosystem == "npm" and name == "@ant-design/icons-svg" and version == "4.4.2":
            files = [ROOT / "frontend/node_modules/@ant-design/icons/LICENSE", directory / "package.json"]
            provenance = "MIT license from sibling @ant-design/icons in the same ant-design-icons repository"
        if not files and ecosystem == "npm" and name == "toggle-selection" and version == "1.0.6":
            files = [ROOT / "packaging/go/licenses/toggle-selection-1.0.6.txt", directory / "package.json", directory / "README.md"]
            provenance = "Published MIT declaration with upstream metadata and standard license terms; see notice"
        if not files:
            raise ValueError(f"No license text found for {ecosystem} {name} {version}")
        for path in files:
            shutil.copy2(path, target / path.name)
        inventory.append({"ecosystem": ecosystem, "name": name, "version": version,
                          "provenance": provenance,
                          "license_files": [f"third-party/{identifier}/{path.name}" for path in files]})
    (destination / "dependencies.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")


def package_guides(bundle):
    guides = {"Go运行包.md": "README.md", "Go备份与恢复.md": "BACKUP.md", "旧版资料导入.md": "IMPORT.md"}
    for source, target in guides.items():
        text = (ROOT / "docs/部署" / source).read_text(encoding="utf-8")
        for original, packaged in guides.items():
            text = text.replace(f"]({original})", f"]({packaged})")
        (bundle / target).write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--go", default="go", help="Go executable path")
    parser.add_argument("--licenses-only", action="store_true", help="Collect dependency notices without compiling release bundles")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/go-release")
    args = parser.parse_args()
    if args.licenses_only:
        destination = args.output.resolve()
        destination.mkdir(parents=True, exist_ok=False)
        copy_licenses(destination, args.go)
        return
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?", version):
        raise ValueError("Invalid VERSION")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise ValueError("npm is required")
    run([npm, "run", "build"], cwd=ROOT / "frontend")
    run([npm, "run", "test:performance"], cwd=ROOT / "frontend")
    run([args.go, "test", "./internal/...", "./cmd/..."])
    run([args.go, "vet", "./internal/...", "./cmd/..."])
    common = output / "common"
    common.mkdir()
    copy_licenses(common, args.go)
    assets = {}
    for platform, archive_suffix, executable in [("windows", "zip", "toolbox.exe"), ("linux", "tar.gz", "toolbox")]:
        name = f"NEU-JWXT-Toolkit-{version}-{platform}-amd64-go"
        bundle = output / name
        bundle.mkdir()
        shutil.copytree(common, bundle / "licenses")
        shutil.copytree(ROOT / "frontend/src/theme/templates", bundle / "themes")
        shutil.copytree(ROOT / "frontend/build", bundle / "frontend/build")
        (bundle / "VERSION").write_bytes((version + "\n").encode("ascii"))
        for source, target in [("LICENSE", "LICENSE"),
                               ("backend/core/course_selection/THIRD_PARTY_NOTICE.md", "THIRD_PARTY_NOTICE.md")]:
            shutil.copy2(ROOT / source, bundle / target)
        package_guides(bundle)
        environment = {**os.environ, "GOOS": platform, "GOARCH": "amd64", "CGO_ENABLED": "0"}
        run([args.go, "build", "-trimpath", "-buildvcs=false", "-ldflags=-s -w", "-o", str(bundle / executable), "./cmd/toolbox"], env=environment)
        (bundle / executable).chmod(0o755)
        if platform == "windows":
            shutil.copy2(ROOT / "packaging/windows/app.ico", bundle / "app.ico")
            (bundle / "启动工具箱.cmd").write_bytes(b'@echo off\r\n"%~dp0toolbox.exe" launch %*\r\nif errorlevel 1 pause\r\n')
        else:
            unit = (ROOT / "packaging/go/neu-toolbox.service").read_text(encoding="utf-8")
            (bundle / "neu-toolbox.service").write_bytes(unit.encode("utf-8"))
        archive = output / f"{name}.{archive_suffix}"
        if platform == "windows":
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as stream:
                for path in sorted(bundle.rglob("*")):
                    if path.is_file():
                        stream.write(path, path.relative_to(output))
        else:
            def linux_permissions(member):
                member.mode = 0o755 if member.isdir() or member.name == f"{name}/toolbox" else 0o644
                member.uid = member.gid = 0
                member.uname = member.gname = "root"
                return member

            with tarfile.open(archive, "w:gz") as stream:
                stream.add(bundle, arcname=name, filter=linux_permissions)
        with archive.open("rb") as archive_file:
            checksum = hashlib.file_digest(archive_file, "sha256").hexdigest()
        assets[f"{platform}-amd64"] = {"name": archive.name, "size": archive.stat().st_size,
                                        "sha256": checksum}
    manifest = {"schema_version": 1, "version": version, "runtime": "go-web", "assets": assets}
    (output / "go-release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output / "SHA256SUMS").write_text("".join(f'{asset["sha256"]}  {asset["name"]}\n' for asset in assets.values()), encoding="utf-8")
    print(f"Built {version} bundles in {output}")


if __name__ == "__main__":
    main()
