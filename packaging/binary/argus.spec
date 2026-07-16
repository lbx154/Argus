# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the binary-only Argus distribution."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parents[1]

datas, binaries, hiddenimports = collect_all("argus_skill", include_py_files=False)
datas += [
    (
        str(ROOT / "frontend" / "tui" / "bundle" / "argus.mjs"),
        "argus_skill/_frontend/tui/bundle",
    ),
    (
        str(ROOT / "frontend" / "web" / "dist"),
        "argus_skill/_frontend/web/dist",
    ),
]

analysis = Analysis(
    [str(ROOT / "packaging" / "binary" / "argus_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "mypy", "ruff"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="argus-core",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
