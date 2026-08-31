# -*- mode: python ; coding: utf-8 -*-

"""PyInstaller specification for the Tauri desktop's frozen backend."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

from argus_skill.domains import BUILTIN_DOMAINS
from argus_skill.skills.vertical_select import VERTICALS
from argus_skill.verticals._base import _VERTICAL_IMPORT_ALIASES

TAURI_ROOT = Path(SPECPATH).resolve()
ROOT = TAURI_ROOT.parent

# Ship every in-tree Python module as source data so the frozen ``-m`` shim
# can execute dynamic tools without forcing PyInstaller to analyze every
# optional scientific/quant dependency at build time. Modules reached by the
# product runtime are still analyzed normally; dynamic providers remain exact
# hidden imports below.
datas = collect_data_files("argus_skill", include_py_files=True)
# Windows does not ship an IANA timezone database. Keep named ZoneInfo keys
# available to the frozen Python-compatible runtime and extension tools.
datas += collect_data_files("tzdata")
web_dist = ROOT / "frontend" / "web" / "dist"
if web_dist.is_dir():
    datas.append((str(web_dist), "argus_skill/_frontend/web/dist"))

tui_bundle = ROOT / "frontend" / "tui" / "bundle" / "argus.mjs"
if tui_bundle.is_file():
    datas.append((str(tui_bundle), "argus_skill/_frontend/tui/bundle"))


def collect_in_tree_modules(package_root, package):
    """List every shipped Python module without importing optional subpackages."""
    modules = []
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root)
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        module = ".".join((package, *parts))
        if module and module not in modules:
            modules.append(module)
    return modules


def collect_provider_modules(root, names, leaf, aliases=None):
    """Collect exact provider leaves without traversing optional helper packages."""
    aliases = aliases or {}
    modules = []
    for name in names:
        import_name = aliases.get(name, name)
        package = f"{root}.{import_name}"
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


argus_modules = collect_in_tree_modules(ROOT / "argus_skill", "argus_skill")

vertical_stage_modules = collect_provider_modules(
    "argus_skill.verticals",
    VERTICALS,
    "stages",
    _VERTICAL_IMPORT_ALIASES,
)
domain_overlay_modules = collect_provider_modules(
    "argus_skill.domains",
    BUILTIN_DOMAINS,
    "overlay",
)

hiddenimports = (
    ["tzdata"]
    + collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("websockets")
    + collect_submodules("multipart")
    + collect_submodules("python_multipart")
    + vertical_stage_modules
    + domain_overlay_modules
)

a = Analysis(
    [str(ROOT / "argus_skill" / "desktop_backend_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
