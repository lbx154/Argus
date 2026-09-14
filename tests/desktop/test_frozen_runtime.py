from __future__ import annotations

import ast
import io
import os
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest
import yaml

from argus import desktop_backend_entry
from argus.apps import tui_launcher
from argus.desktop_backend_entry import (
    _install_windows_signal_zero_guard,
    _python_compat_entrypoint,
    verify_runtime_providers,
)
from argus.domains import BUILTIN_DOMAINS, load_domain
from argus.skills.vertical_select import VERTICALS
from argus.verticals._base import load_vertical

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "desktop-tauri" / "argus_backend.spec"


def test_desktop_multicommand_test_step_fails_on_first_error() -> None:
    workflow = (ROOT / ".github" / "workflows" / "extended.yml").read_text(
        encoding="utf-8"
    )
    step = workflow.split("- name: Lint and test desktop sources", 1)[1].split(
        "- name: Build frozen backend and unsigned Tauri package layout", 1
    )[0]

    assert "shell: bash" in step


@pytest.mark.parametrize("failed_stage", ["backend", "web", "desktop", "none"])
def test_desktop_build_stops_on_each_failed_command(tmp_path, failed_stage) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is required to execute the Windows CI build step")
    workflow = yaml.safe_load((ROOT / ".github/workflows/extended.yml").read_text(encoding="utf-8"))
    step = next(step for step in workflow["jobs"]["desktop"]["steps"]
                if step.get("name") == "Build frozen backend and unsigned Tauri package layout")
    backend = tmp_path / "desktop-tauri/scripts/build-backend.ps1"
    backend.parent.mkdir(parents=True)
    backend.write_text(
        "Add-Content -LiteralPath stages.txt -Value backend\n"
        f"$global:LASTEXITCODE = {19 if failed_stage == 'backend' else 0}\n",
        encoding="utf-8",
    )
    # Execute the actual workflow with deterministic stand-ins for costly builds.
    # A later successful command must never turn an earlier failure green.
    script = f"""
$ErrorActionPreference = 'Stop'
$failedStage = '{failed_stage}'
function npm {{
    $stage = if ($args -contains 'frontend/web') {{ 'web' }} else {{ 'desktop' }}
    Add-Content -LiteralPath stages.txt -Value $stage
    $global:LASTEXITCODE = if ($stage -eq $failedStage) {{ 19 }} else {{ 0 }}
}}
{step['run']}
Add-Content -LiteralPath stages.txt -Value complete
"""
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    stages = (tmp_path / "stages.txt").read_text(encoding="utf-8-sig").splitlines()
    expected = ["backend", "web", "desktop", "complete"]
    if failed_stage != "none":
        expected = expected[:expected.index(failed_stage) + 1]
    assert result.returncode == (0 if failed_stage == "none" else 19), result.stderr
    assert stages == expected


def _execute_spec_collection(tree: ast.Module) -> tuple[dict, list[tuple[str, str]]]:
    prefix: list[ast.stmt] = []
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "PyInstaller.utils.hooks"
        ):
            continue
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "a"
            for target in node.targets
        ):
            break
        prefix.append(node)

    calls: list[tuple[str, str]] = []

    def collect_submodules(
        package: str,
        filter=lambda name: True,
        on_error: str = "warn once",
    ) -> list[str]:
        calls.append((package, on_error))
        candidates = [package]
        if package.startswith("argus.verticals."):
            candidates += [f"{package}.stages", f"{package}.helper"]
        elif package.startswith("argus.domains."):
            candidates += [f"{package}.overlay", f"{package}.helper"]
        return [name for name in candidates if filter(name)]

    namespace = {
        "SPECPATH": str(ROOT / "desktop-tauri"),
        "collect_data_files": lambda package, **kwargs: [
            (f"{package}-python-sources", str(bool(kwargs.get("include_py_files"))))
        ],
        "collect_submodules": collect_submodules,
    }
    module = ast.fix_missing_locations(ast.Module(body=prefix, type_ignores=[]))
    exec(compile(module, str(SPEC_PATH), "exec"), namespace)  # noqa: S102
    return namespace, calls


def test_windows_signal_zero_guard_never_delegates_to_terminate_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delegated: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: delegated.append((pid, sig)))
    monkeypatch.setattr(
        "argus.core.daemon_lock.is_pid_running",
        lambda pid: pid == 123,
    )

    _install_windows_signal_zero_guard(platform_name="nt")

    os.kill(123, 0)
    with pytest.raises(ProcessLookupError):
        os.kill(456, 0)
    os.kill(123, 15)
    assert delegated == [(123, 15)]


def test_frozen_python_compat_dispatches_argus_modules_and_code(capsys) -> None:
    handled, code = _python_compat_entrypoint([
        "-I",
        "-m",
        "argus.tools.manager_live_view",
        "--help",
    ])
    assert handled is True and code == 0
    assert "manager_live_view" in capsys.readouterr().out

    handled, code = _python_compat_entrypoint(["-c", "print('compat-ok')"])
    assert handled is True and code == 0
    assert capsys.readouterr().out.strip() == "compat-ok"

    handled, code = _python_compat_entrypoint(["-m", "pip", "--version"])
    assert handled is True and code == 2
    assert "refusing non-Argus" in capsys.readouterr().err


def _frozen_console_streams(monkeypatch, encoding, *, platform_name="nt"):
    stdout = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict", newline="\n", write_through=True)
    stderr = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict", newline="\n", write_through=True)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop_backend_entry, "verify_runtime_providers", lambda: {"ok": True})
    monkeypatch.setattr(desktop_backend_entry, "_install_windows_signal_zero_guard", lambda: None)
    monkeypatch.setattr(
        tui_launcher, "_configure_windows_console_encoding",
        partial(tui_launcher._configure_windows_console_encoding, platform_name=platform_name),
    )
    return stdout, stderr


@pytest.mark.parametrize("encoding", ["cp1252", "gbk"])
def test_frozen_windows_code_keeps_multilingual_stdout_and_stderr(monkeypatch, encoding):
    stdout, stderr = _frozen_console_streams(monkeypatch, encoding)
    message = "Copilot 正在安装… 🧪"
    monkeypatch.setattr(sys, "argv", [
        "argus-backend", "-c",
        f"import sys; print({message!r}); print({message!r}, file=sys.stderr)",
    ])

    assert desktop_backend_entry._entrypoint() == 0
    assert sys.stdout is stdout and sys.stderr is stderr
    assert stdout.buffer.getvalue().decode("utf-8") == message + "\n"
    assert stderr.buffer.getvalue().decode("utf-8") == message + "\n"


@pytest.mark.parametrize("encoding", ["cp1252", "gbk"])
def test_frozen_non_windows_code_preserves_the_existing_stream_encoding(monkeypatch, encoding):
    stdout, stderr = _frozen_console_streams(monkeypatch, encoding, platform_name="posix")
    monkeypatch.setattr(sys, "argv", [
        "argus-backend", "-c", "import sys; print('ready'); print('diagnostic', file=sys.stderr)",
    ])

    assert desktop_backend_entry._entrypoint() == 0
    assert sys.stdout is stdout and sys.stderr is stderr
    assert stdout.encoding == encoding and stderr.encoding == encoding
    assert stdout.buffer.getvalue() == b"ready\n"
    assert stderr.buffer.getvalue() == b"diagnostic\n"


@pytest.mark.parametrize("encoding", ["cp1252", "gbk"])
def test_frozen_cached_native_install_reports_progress_on_redirected_windows_streams(
    tmp_path, monkeypatch, encoding,
):
    from argus.trial import native_cli

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setattr(native_cli.platform, "system", lambda: "Windows")
    monkeypatch.setattr(native_cli.platform, "machine", lambda: "AMD64")
    executable = tmp_path / "runtime" / "copilot" / native_cli.VERSION / "copilot.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture-native-cli")
    verified = []
    monkeypatch.setattr(native_cli, "verify_cli", lambda path: verified.append(path))
    monkeypatch.setattr(
        native_cli.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("Cached CLI must not download"),
    )
    stdout, _stderr = _frozen_console_streams(monkeypatch, encoding)
    monkeypatch.setattr(sys, "argv", [
        "argus-backend", "-c",
        "from argus.trial.native_cli import install_native_copilot; "
        "install_native_copilot(); print('native-copilot-ready')",
    ])

    assert desktop_backend_entry._entrypoint() == 0
    assert verified == [executable]
    assert stdout.buffer.getvalue().decode("utf-8").splitlines() == [
        "正在检查已安装的 Copilot…", "native-copilot-ready",
    ]


def test_frozen_python_compat_runs_unittest_with_standard_exit_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_passing.py").write_text(
        "import unittest\n\n"
        "class PassingTest(unittest.TestCase):\n"
        "    def test_passes(self):\n"
        "        self.assertEqual(2 + 2, 4)\n",
        encoding="utf-8",
    )
    (tmp_path / "test_failing.py").write_text(
        "import unittest\n\n"
        "class FailingTest(unittest.TestCase):\n"
        "    def test_fails(self):\n"
        "        self.assertEqual(2 + 2, 5)\n",
        encoding="utf-8",
    )

    assert _python_compat_entrypoint(
        ["-m", "unittest", "test_passing.py"]
    ) == (True, 0)
    assert _python_compat_entrypoint(
        ["-m", "unittest", "test_failing.py"]
    ) == (True, 1)


def test_frozen_python_compat_runs_stdin_with_python_argv_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stdin_sibling.py").write_text(
        "VALUE = 'stdin-sibling-ok'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            "import sys\n"
            "from stdin_sibling import VALUE\n"
            "print(VALUE, sys.argv)\n"
            "assert __file__ == '<stdin>'\n"
        ),
    )
    original_argv = list(sys.argv)
    original_path = list(sys.path)

    handled, code = _python_compat_entrypoint(["-", "one", "two"])

    assert handled is True and code == 0
    assert "stdin-sibling-ok ['-', 'one', 'two']" in capsys.readouterr().out
    assert sys.argv == original_argv
    assert sys.path == original_path


def test_frozen_python_compat_returns_stdin_system_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("raise SystemExit(7)\n"))

    assert _python_compat_entrypoint(["-"]) == (True, 7)


def test_frozen_python_compat_runs_scripts_with_python_argv_semantics(
    tmp_path: Path,
    capsys,
) -> None:
    helper = tmp_path / "sibling_helper.py"
    helper.write_text("VALUE = 'sibling-ok'\n", encoding="utf-8")
    script = tmp_path / "script with spaces.py"
    script.write_text(
        "import sys\n"
        "from sibling_helper import VALUE\n"
        "print(VALUE, sys.argv[1:])\n",
        encoding="utf-8",
    )
    original_argv = list(sys.argv)
    original_path = list(sys.path)

    handled, code = _python_compat_entrypoint([str(script), "one", "two"])

    assert handled is True and code == 0
    assert "sibling-ok ['one', 'two']" in capsys.readouterr().out
    assert sys.argv == original_argv
    assert sys.path == original_path


def test_frozen_python_compat_dispatches_daemon_spawn_helper(monkeypatch) -> None:
    calls: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(
        "argus.desktop_backend_entry.runpy.run_module",
        lambda module, *, run_name, alter_sys: calls.append(
            (module, run_name, alter_sys)
        ),
    )

    handled, code = _python_compat_entrypoint([
        "-m",
        "argus.daemon.spawn_helper",
    ])

    assert handled is True and code == 0
    assert calls == [("argus.daemon.spawn_helper", "__main__", True)]


@pytest.mark.parametrize(
    ("requested", "canonical"),
    [
        ("argus_skill", "argus"),
        ("argus_skill.daemon.spawn_helper", "argus.daemon.spawn_helper"),
        ("argus_skill.tools.subagent", "argus.tools.subagent"),
    ],
)
def test_frozen_python_compat_accepts_the_pre_rename_module_spelling(
    monkeypatch, requested: str, canonical: str
) -> None:
    """Seeded Skill copies still say ``-m argus_skill.…``; the frozen backend runs them as ``argus.…``."""
    calls: list[str] = []
    monkeypatch.setattr(
        "argus.desktop_backend_entry.runpy.run_module",
        lambda module, *, run_name, alter_sys: calls.append(module),
    )

    handled, code = _python_compat_entrypoint(["-m", requested, "--help"])

    assert handled is True and code == 0
    assert calls == [canonical]


def test_frozen_python_compat_still_refuses_lookalike_packages(capsys) -> None:
    handled, code = _python_compat_entrypoint(["-m", "argus_skillful.tool"])

    assert (handled, code) == (True, 2)
    assert "refusing non-Argus frozen module 'argus_skillful.tool'" in capsys.readouterr().err


def test_source_runtime_verifier_loads_every_registered_provider() -> None:
    report = verify_runtime_providers()

    assert report["ok"] is True
    assert report["verticals"] == {
        "expected": list(VERTICALS),
        "loaded": list(VERTICALS),
    }
    assert report["domains"] == {
        "expected": list(BUILTIN_DOMAINS),
        "loaded": list(BUILTIN_DOMAINS),
    }
    assert report["failures"] == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native payload requirement")
def test_pyinstaller_spec_rejects_a_missing_native_adapter(monkeypatch) -> None:
    native = ROOT / "argus/_native/platon-headless.exe"
    original = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: False if path == native else original(path))
    tree = ast.parse(SPEC_PATH.read_text(encoding="utf-8"), filename=str(SPEC_PATH))
    with pytest.raises(RuntimeError, match="Build the first-party Windows adapter"):
        _execute_spec_collection(tree)


def test_pyinstaller_spec_collects_registered_stage_and_overlay_modules(monkeypatch) -> None:
    tree = ast.parse(SPEC_PATH.read_text(encoding="utf-8"), filename=str(SPEC_PATH))
    native = ROOT / "argus/_native/platon-headless.exe"
    original = Path.is_file
    # Collection-only unit test: do not require a previous build in the checkout.
    # The actual spec still refuses missing payloads, as tested separately above.
    monkeypatch.setattr(Path, "is_file", lambda path: True if path == native else original(path))
    namespace, calls = _execute_spec_collection(tree)
    if sys.platform == "win32":
        assert (str(native), "argus/_native") in namespace["datas"]
    expected_verticals = [load_vertical(name).__name__ for name in VERTICALS]
    expected_domains = [load_domain(name).__name__ for name in BUILTIN_DOMAINS]

    assert namespace["vertical_stage_modules"] == expected_verticals
    assert "argus.verticals.math_synth.stages" in expected_verticals
    assert namespace["domain_overlay_modules"] == expected_domains
    assert set(expected_verticals + expected_domains) <= set(namespace["hiddenimports"])
    assert "unittest" in namespace["hiddenimports"]
    # The pre-rename alias ships in the frozen build for one release.
    assert {"argus_skill", "argus_skill.__main__"} <= set(namespace["hiddenimports"])
    assert ("unittest", "warn once") in calls
    assert "argus.tools.manager_live_view" in namespace["argus_modules"]
    assert "argus.daemon.spawn_helper" in namespace["argus_modules"]
    assert ("argus-python-sources", "True") in namespace["datas"]
    # Dynamic tools are shipped as source data rather than hidden imports, so
    # optional scientific modules fail visibly only when invoked and do not
    # drag the host environment into every desktop build.
    assert "argus.tools.manager_live_view" not in namespace["hiddenimports"]

    provider_calls = [
        call
        for call in calls
        if call[0].startswith("argus.verticals.")
        or call[0].startswith("argus.domains.")
    ]
    assert len(provider_calls) == len(VERTICALS) + len(BUILTIN_DOMAINS)
    assert all(on_error == "raise" for _package, on_error in provider_calls)
