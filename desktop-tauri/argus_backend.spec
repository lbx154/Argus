# -*- mode: python ; coding: utf-8 -*-

"""PyInstaller specification for the Tauri desktop's frozen backend."""

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

from argus.domains import BUILTIN_DOMAINS
from argus.skills.vertical_select import VERTICALS

TAURI_ROOT = Path(SPECPATH).resolve()
ROOT = TAURI_ROOT.parent

# Ship every in-tree Python module as source data so the frozen ``-m`` shim
# can execute dynamic tools without forcing PyInstaller to analyze every
# optional scientific/quant dependency at build time. Modules reached by the
# product runtime are still analyzed normally; dynamic providers remain exact
# hidden imports below.
optional_roots = [p.parent for p in (ROOT / "argus/verticals").glob("*/workbench.json")]
def optional_source(path):
    path = Path(path).resolve()
    return any(path == root or root in path.parents for root in optional_roots)

datas = [(source, target) for source, target in collect_data_files("argus", include_py_files=True)
         if not optional_source(source)]
datas.append((str(ROOT / "packages/contracts/schemas"), "argus/_contracts"))
# Windows does not ship an IANA timezone database. Keep named ZoneInfo keys
# available to the frozen Python-compatible runtime and extension tools.
datas += collect_data_files("tzdata")
if sys.platform == "win32":
    platon_runner = ROOT / "argus" / "_native" / "platon-headless.exe"
    if not platon_runner.is_file():
        raise RuntimeError("Build the first-party Windows adapter with scripts/build-native-tools.ps1 first")
    datas.append((str(platon_runner), "argus/_native"))
web_dist = ROOT / "frontend" / "web" / "dist"
if web_dist.is_dir():
    datas.append((str(web_dist), "argus/_frontend/web/dist"))

tui_bundle = ROOT / "frontend" / "tui" / "bundle" / "argus.mjs"
if tui_bundle.is_file():
    datas.append((str(tui_bundle), "argus/_frontend/tui/bundle"))


def collect_in_tree_modules(package_root, package):
    """List every shipped Python module without importing optional subpackages."""
    modules = []
    for path in sorted(package_root.rglob("*.py")):
        if optional_source(path):
            continue
        relative = path.relative_to(package_root)
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        module = ".".join((package, *parts))
        if module and module not in modules:
            modules.append(module)
    return modules


def collect_provider_modules(root, names, leaf):
    """Collect exact provider leaves without traversing optional helper packages."""
    modules = []
    for name in names:
        package = f"{root}.{name}"
        target = f"{package}.{leaf}"
        discovered = collect_submodules(
            package,
            filter=lambda candidate, target=target: candidate == target,
            on_error="raise",
        )
        if target not in discovered:
            raise RuntimeError(f"PyInstaller could not collect provider module {target}")
        modules.append(target)
    return modules


argus_modules = collect_in_tree_modules(ROOT / "argus", "argus")

# Built-in verticals only, on purpose: the frozen bundle ships the in-tree
# providers; community verticals (``argus-verticals``) are entry points of a
# separately installed distribution and are not part of the desktop build.
vertical_stage_modules = collect_provider_modules(
    "argus.verticals",
    VERTICALS,
    "stages",
)
domain_overlay_modules = collect_provider_modules(
    "argus.domains",
    BUILTIN_DOMAINS,
    "overlay",
)

hiddenimports = (
    ["tzdata", "argus.trial.desktop", "certifi"]
    # The pre-rename import alias: ``-c "from argus_skill... import"`` snippets
    # from older daemons and Skill copies still resolve in the frozen backend.
    + ["argus_skill", "argus_skill.__main__"]
    + collect_submodules("unittest")
    + collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("websockets")
    + collect_submodules("multipart")
    + collect_submodules("python_multipart")
    + vertical_stage_modules
    + domain_overlay_modules
)

a = Analysis(
    [str(ROOT / "argus" / "desktop_backend_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["argus.verticals." + p.name for p in optional_roots],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="argus-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="argus-backend",
)
