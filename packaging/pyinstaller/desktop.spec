# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

ROOT = Path(SPECPATH).parents[1]

a = Analysis(
    [str(ROOT / "launchers" / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "frontend" / "build"), "frontend/build"),
        (str(ROOT / "VERSION"), "."),
    ],
    # Backend imports are statically discoverable, including the few imports
    # placed inside properties/functions. Collecting every backend submodule
    # also pulled development-only modules into the launcher and made the
    # frozen image unnecessarily opaque to security scanners.
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # anyio exposes optional pytest helpers when pytest happens to be installed
    # in the build environment. They are never used by the application.
    excludes=["pytest", "_pytest"],
    # Keep Python bytecode as individual files in the onedir _internal tree.
    # This trades some file count for a more inspectable bundle and avoids one
    # large opaque PYZ payload in the executable.
    noarchive=True,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NEU-JWXT-Toolkit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="NEU-JWXT-Toolkit",
)
