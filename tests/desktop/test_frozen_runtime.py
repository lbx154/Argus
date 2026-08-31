from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

from argus_skill.desktop_backend_entry import (
    _install_windows_signal_zero_guard,
    _python_compat_entrypoint,
    verify_runtime_providers,
)
from argus_skill.domains import BUILTIN_DOMAINS, load_domain
from argus_skill.skills.vertical_select import VERTICALS
from argus_skill.verticals._base import load_vertical

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "desktop-tauri" / "argus_backend.spec"


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
        if package.startswith("argus_skill.verticals."):
            candidates += [f"{package}.stages", f"{package}.helper"]
        elif package.startswith("argus_skill.domains."):
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
        "argus_skill.core.daemon_lock.is_pid_running",
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
        "argus_skill.tools.manager_live_view",
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
        "argus_skill.desktop_backend_entry.runpy.run_module",
        lambda module, *, run_name, alter_sys: calls.append(
            (module, run_name, alter_sys)
        ),
    )

    handled, code = _python_compat_entrypoint([
        "-m",
        "argus_skill.daemon.spawn_helper",
    ])

    assert handled is True and code == 0
    assert calls == [("argus_skill.daemon.spawn_helper", "__main__", True)]


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


def test_pyinstaller_spec_collects_registered_stage_and_overlay_modules() -> None:
    tree = ast.parse(SPEC_PATH.read_text(encoding="utf-8"), filename=str(SPEC_PATH))

    namespace, calls = _execute_spec_collection(tree)
    expected_verticals = [load_vertical(name).__name__ for name in VERTICALS]
    expected_domains = [load_domain(name).__name__ for name in BUILTIN_DOMAINS]

    assert namespace["vertical_stage_modules"] == expected_verticals
    assert "argus_skill.verticals.digital_circuit.benchmark.stages" in expected_verticals
    assert namespace["domain_overlay_modules"] == expected_domains
    assert set(expected_verticals + expected_domains) <= set(namespace["hiddenimports"])
    assert "argus_skill.tools.manager_live_view" in namespace["argus_modules"]
    assert "argus_skill.daemon.spawn_helper" in namespace["argus_modules"]
    assert ("argus_skill-python-sources", "True") in namespace["datas"]
    # Dynamic tools are shipped as source data rather than hidden imports, so
    # optional scientific modules fail visibly only when invoked and do not
    # drag the host environment into every desktop build.
    assert "argus_skill.tools.manager_live_view" not in namespace["hiddenimports"]

    provider_calls = [
        call
        for call in calls
        if call[0].startswith("argus_skill.verticals.")
        or call[0].startswith("argus_skill.domains.")
    ]
    assert len(provider_calls) == len(VERTICALS) + len(BUILTIN_DOMAINS)
    assert all(on_error == "raise" for _package, on_error in provider_calls)
