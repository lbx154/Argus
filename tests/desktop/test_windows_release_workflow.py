"""Keep the authorized Windows-only release and frozen build-input boundaries."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _workflow():
    # BaseLoader preserves GitHub's `on` key rather than YAML 1.1's boolean.
    return yaml.load(
        (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def _step(name):
    return next(step for step in _workflow()["jobs"]["desktop"]["steps"] if step.get("name") == name)


def _action(name):
    return next(step for step in _workflow()["jobs"]["desktop"]["steps"] if step.get("uses") == name)


def test_windows_only_tags_cannot_trigger_the_all_platform_release():
    workflow = _workflow()
    tags = workflow["on"]["push"]["tags"]
    assert tags[0] == "v*"
    for tag in ("v0.1.7", "v0.1.8"):
        assert tags.index("!" + tag) > tags.index("v*")
    assert workflow["jobs"]["pypi"]["if"].startswith("false &&")
    assert "startsWith(github.ref, 'refs/tags/v')" in workflow["jobs"]["github"]["if"]
    assert workflow["permissions"] == {"contents": "read"}


def test_windows_dispatch_selects_one_explicit_msvc_runner():
    workflow = _workflow()
    assert "windows-x86_64" in workflow["on"]["workflow_dispatch"]["inputs"]["platform"]["options"]
    matrix = workflow["jobs"]["desktop"]["strategy"]["matrix"]["include"]
    selected = re.search(r"inputs.platform == 'windows-x86_64' && '([^']+)'", matrix)
    assert selected is not None
    assert json.loads(selected.group(1)) == [{"os": "windows-2022", "platform": "windows-x86_64"}]
    assert "github.event_name != 'workflow_dispatch' || inputs.platform == 'all'" == workflow["jobs"]["python"]["if"]


def test_windows_toolchain_pins_leave_other_platform_inputs_unchanged():
    assert _action("actions/setup-node@v4")["with"]["node-version"] == "${{ runner.os == 'Windows' && '24.19.0' || '22.12.0' }}"
    assert _action("actions/setup-python@v5")["with"]["python-version"] == "${{ runner.os == 'Windows' && '3.11.9' || '3.12' }}"
    assert _action("dtolnay/rust-toolchain@stable")["with"]["toolchain"] == "${{ runner.os == 'Windows' && '1.98.0-x86_64-pc-windows-msvc' || 'stable' }}"
    uv = _action("astral-sh/setup-uv@v5")
    assert uv["if"] == "runner.os == 'Windows'"
    assert uv["with"]["version"] == "0.12.16"
    other = _step("Install non-Windows Python build dependencies")
    assert other["if"] == "runner.os != 'Windows'"
    assert 'python -m pip install -e ".[trial]" "pyinstaller>=6.11,<7" tzdata' == other["run"]


def test_windows_dependencies_are_frozen_and_build_isolation_cannot_resolve_new_tools():
    step = _step("Install frozen Windows build dependencies")
    assert step["if"] == "runner.os == 'Windows'"
    assert step["env"]["UV_PYTHON_DOWNLOADS"] == "never"
    script = step["run"]
    assert "uv sync --frozen --extra dev --extra trial --no-install-project --python 3.11.9" in script
    assert "--no-deps -r desktop-tauri/scripts/windows-build-requirements.txt" in script
    assert "--no-deps --no-build-isolation --editable ." in script
    assert "uv pip check --python .venv/Scripts/python.exe" in script
    assert "cargo fetch --locked" in script
    assert "--target x86_64-pc-windows-msvc" in script
    assert "RUSTUP_TOOLCHAIN=1.98.0-x86_64-pc-windows-msvc" in script
    assert "--upgrade" not in script
    frontend = _step("Install frontend build dependencies")["run"]
    for package in ("frontend/web", "frontend/tui", "desktop-tauri"):
        assert f"npm --prefix {package} ci" in frontend


def test_windows_build_tool_pins_match_existing_locked_support_packages():
    path = ROOT / "desktop-tauri/scripts/windows-build-requirements.txt"
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, version = line.split("==")
        assert name not in pins
        assert re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", version)
        pins[name] = version
    assert pins["pyinstaller"] == "6.22.3"
    assert pins["pyinstaller-hooks-contrib"] == "2026.7"
    locked = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]
    for name in ("hatchling", "packaging", "pathspec", "pluggy", "tomlkit", "trove-classifiers"):
        assert pins[name] == next(package["version"] for package in locked if package["name"] == name)


def test_windows_tauri_build_passes_locked_to_cargo_after_tauri_options():
    source = (ROOT / "desktop-tauri/scripts/desktop-build.mjs").read_text(encoding="utf-8")
    locked = "if (process.platform === 'win32') args.push('--', '--locked');"
    assert locked in source
    assert source.index("args.push('--config'") < source.index(locked)
    assert source.index(locked) < source.index("'node_modules/@tauri-apps/cli/tauri.js'")


def test_signing_configuration_remains_scoped_to_the_existing_signing_step():
    steps = _workflow()["jobs"]["desktop"]["steps"]
    configured = [step for step in steps if "TAURI_SIGNING_PRIVATE_KEY" in step.get("env", {})]
    assert len(configured) == 1
    assert configured[0]["name"] == "Build signed desktop updates"
    assert configured[0]["env"]["TAURI_SIGNING_PRIVATE_KEY"] == "${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}"
    assert 'test -n "$TAURI_SIGNING_PRIVATE_KEY"' in configured[0]["run"]
    assert _step("Verify Windows staging and signature boundary")["run"] == "npm --prefix desktop-tauri run test:release"


def test_source_or_artifact_drift_stops_windows_artifact_upload():
    steps = _workflow()["jobs"]["desktop"]["steps"]
    gate = _step("Verify reviewed Windows source and artifacts")
    assert gate["if"] == "runner.os == 'Windows'"
    for check in (
        "argus.release_tools.generate_manifest --check",
        "argus.release_tools.check_artifacts",
        "git diff --exit-code HEAD",
        "git ls-files --others --exclude-standard -- argus frontend",
        "exit 1",
    ):
        assert check in gate["run"]
    upload = _action("actions/upload-artifact@v4")
    assert steps.index(gate) < steps.index(upload)
    assert upload["with"]["name"] == "desktop-${{ matrix.platform }}"
    assert upload["with"]["if-no-files-found"] == "error"
