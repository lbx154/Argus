"""Build the Windows backend in fresh output paths, never replacing old payloads."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def isolated_environment(work: Path) -> dict[str, str]:
    original = os.environ.copy()
    user_home = original.get("USERPROFILE", str(Path.home()))
    env = {
        name: value for name, value in original.items()
        if not re.search(r"API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|^ARGUS_|^PI_|^TAURI_SIGNING_", name, re.I)
    }
    for directory in ("home", "tmp", "roaming", "local", "state"):
        (work / directory).mkdir()
    env.update({
        "HOME": str(work / "home"), "USERPROFILE": str(work / "home"),
        "APPDATA": str(work / "roaming"), "LOCALAPPDATA": str(work / "local"),
        "TEMP": str(work / "tmp"), "TMP": str(work / "tmp"), "TMPDIR": str(work / "tmp"),
        "ARGUS_SKILL_HOME": str(work / "state"), "ARGUS_WORKBENCH_HOST_ROOT": str(work / "state"),
        "ARGUS_SKILL_SAFE_MODE": "1",
        "CODEX_HOME": str(work / "codex"), "COPILOT_HOME": str(work / "copilot"),
        "CLAUDE_CONFIG_DIR": str(work / "claude"), "PI_CODING_AGENT_DIR": str(work / "pi"),
        "XDG_CONFIG_HOME": str(work / "config"), "XDG_CACHE_HOME": str(work / "cache"),
        "XDG_DATA_HOME": str(work / "data"),
        "CARGO_HOME": original.get("CARGO_HOME", str(Path(user_home) / ".cargo")),
        "RUSTUP_HOME": original.get("RUSTUP_HOME", str(Path(user_home) / ".rustup")),
        "RUSTUP_TOOLCHAIN": "stable-x86_64-pc-windows-msvc",
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(REPO), "PATH": str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", ""),
        "PYINSTALLER_CONFIG_DIR": str(work / "pyinstaller-cache"),
        "NPM_CONFIG_USERCONFIG": str(work / "npm-user.ini"),
        "NPM_CONFIG_GLOBALCONFIG": str(work / "npm-global.ini"),
        "NPM_CONFIG_CACHE": str(work / "npm-cache"), "PSModuleAnalysisCachePath": "NUL",
    })
    return env


def assert_release_versions(repo: Path) -> str:
    version = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise RuntimeError("Windows release requires a stable three-part version.")
    values = {}
    for directory in ("frontend/core", "frontend/web", "frontend/tui", "desktop-tauri"):
        filename = f"{directory}/package.json"
        values[filename] = json.loads((repo / filename).read_text(encoding="utf-8"))["version"]
        lock = repo / directory / "package-lock.json"
        if lock.is_file():
            payload = json.loads(lock.read_text(encoding="utf-8"))
            values[f"{directory}/package-lock.json"] = payload["version"]
            values[f"{directory}/package-lock.json root"] = payload["packages"][""]["version"]
    for filename in ("desktop-tauri/src-tauri/tauri.conf.json", "plugins/argus/.claude-plugin/plugin.json"):
        values[filename] = json.loads((repo / filename).read_text(encoding="utf-8"))["version"]
    marketplace = json.loads((repo / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    values["marketplace"] = next(plugin["version"] for plugin in marketplace["plugins"] if plugin["name"] == "argus")
    cargo = tomllib.loads((repo / "desktop-tauri/src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    values["Cargo.toml"] = cargo["package"]["version"]
    for filename, name in (("uv.lock", "argus-skill"), ("desktop-tauri/src-tauri/Cargo.lock", "argus-desktop")):
        lock = tomllib.loads((repo / filename).read_text(encoding="utf-8"))
        values[filename] = next(package["version"] for package in lock["package"] if package["name"] == name)
    runtime = (repo / "argus_skill/__init__.py").read_text(encoding="utf-8")
    runtime_version = re.search(r'^__version__ = "([^"]+)"$', runtime, re.MULTILINE)
    values["Python runtime"] = runtime_version.group(1) if runtime_version else None
    if any(value != version for value in values.values()):
        raise RuntimeError("Windows release version drift: " + ", ".join(name for name, value in values.items() if value != version))
    return version


def validate_payload_identity(source: Path, repo: Path = REPO) -> None:
    frozen = json.loads((source / "_internal/argus_skill/release_manifest.json").read_text(encoding="utf-8"))
    expected = json.loads((repo / "argus_skill/release_manifest.json").read_text(encoding="utf-8"))
    for name in ("package_version", "release_id", "source_digest"):
        if not expected.get(name) or frozen.get(name) != expected[name]:
            raise RuntimeError("Frozen backend identity differs from the reviewed source; use a fresh build.")


def validate_frozen_inputs(source: Path, repo: Path = REPO) -> None:
    expected = json.loads((repo / "argus_skill/release_manifest.json").read_text(encoding="utf-8"))["release_id"]
    package = source / "_internal/argus_skill"
    tui = package / "_frontend/tui/bundle/argus.mjs"
    web = (package / "_frontend/web/dist").resolve()
    if expected not in tui.read_text(encoding="utf-8"):
        raise RuntimeError("Frozen TUI bundle has a different release identity.")
    index = (web / "index.html").read_text(encoding="utf-8")
    entries = re.findall(r'(?:src|href)="([^"]+\.js)"', index)
    current = False
    for reference in entries:
        asset = (web / reference.lstrip("/")).resolve()
        if not asset.is_relative_to(web):
            raise RuntimeError("Frozen Web entry escapes its artifact directory.")
        contents = asset.read_text(encoding="utf-8")
        current = current or expected in contents
    if not current:
        raise RuntimeError("Frozen Web bundle has a different release identity.")
    if tui.read_bytes() != (repo / "frontend/tui/bundle/argus.mjs").read_bytes():
        raise RuntimeError("Frozen TUI bytes differ from the reviewed build.")
    source_web = repo / "frontend/web/dist"
    for path in source_web.rglob("*"):
        if path.is_file():
            shipped = web / path.relative_to(source_web)
            if not shipped.is_file() or shipped.read_bytes() != path.read_bytes():
                raise RuntimeError("Frozen Web bytes differ from the reviewed build.")
    optional = [path.parent for path in (repo / "argus_skill/verticals").glob("*/workbench.json")]
    modules = 0
    for path in (repo / "argus_skill").rglob("*.py"):
        if any(path.is_relative_to(root) for root in optional):
            continue
        shipped = package / path.relative_to(repo / "argus_skill")
        if not shipped.is_file() or shipped.read_bytes() != path.read_bytes():
            raise RuntimeError("Frozen first-party source differs from the reviewed checkout.")
        modules += 1
    if not modules:
        raise RuntimeError("Frozen package has no reviewed first-party modules.")
    print(f"Frozen source and Web/TUI identities verified ({modules} first-party modules).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("This entry point is only for Windows.")
    source = ROOT / "build/argus-backend"
    executable = source / "argus-backend.exe"
    if args.prepare_only:
        if not executable.is_file():
            raise RuntimeError("Frozen backend missing. Build in a fresh Windows workspace first.")
    elif source.exists():
        raise RuntimeError("Backend output already exists. Use a fresh workspace; old build files will not be deleted.")
    assert_release_versions(REPO)
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node.js is required for the release frontends and verified payload staging.")
    (ROOT / "build").mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="windows-freeze-", dir=ROOT / "build"))
    env = isolated_environment(work)

    def run(*command: str) -> None:
        subprocess.run(command, cwd=REPO, env=env, stdin=subprocess.DEVNULL, check=True)

    if not args.prepare_only:
        # A Windows frozen backend must never capture an older checked-in Web
        # bundle. Refresh identity + Web/TUI as one chain before freezing.
        run(sys.executable, "-m", "argus_skill.release_tools.build_release")
        run("powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(ROOT / "scripts/build-native-tools.ps1"))
        run(sys.executable, "-m", "PyInstaller", str(ROOT / "argus_backend.spec"),
            "--distpath", str(ROOT / "build"), "--workpath", str(work / "pyinstaller-work"))
        for arguments in (
            ["--verify-frozen-runtime"],
            ["-I", "-m", "argus_skill.tools.manager_live_view", "--help"],
            ["-c", "import argus_skill.trial.desktop, certifi; print('desktop-trial-ready')"],
            ["-c", "from zoneinfo import ZoneInfo; assert ZoneInfo('Asia/Shanghai').key == 'Asia/Shanghai'"],
        ):
            run(str(executable), *arguments)
        probe = work / "script-probe.py"
        with probe.open("x", encoding="utf-8") as stream:
            stream.write("import argus_skill; print('script-ok', argus_skill.__version__)\n")
        run(str(executable), str(probe))
    run(sys.executable, "-m", "argus_skill.release_tools.generate_manifest", "--check")
    run(sys.executable, "-m", "argus_skill.release_tools.check_artifacts")
    validate_payload_identity(source)
    validate_frozen_inputs(source, REPO)
    run(node, str(ROOT / "scripts/stage-backend.mjs"), str(source), str(ROOT / "resources/argus-backend"))
    print(f"Verified Windows backend ready: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
