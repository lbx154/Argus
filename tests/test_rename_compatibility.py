"""The 2026-09-14 rename ``argus_skill`` -> ``argus``: the old name is the same package for one release.

``argus_skill/`` is a two-file alias (``__init__.py`` installs a ``sys.meta_path``
finder, ``__main__.py`` delegates). These tests pin what "alias" has to mean for
the processes that still carry the old spelling: identity of module objects
(so ``monkeypatch`` and ``isinstance`` agree), ``python -m argus_skill…``
re-entry, the ``argus-skill`` console script, and the argv matchers that
recognise teammates spawned before the rename.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import argus
import argus.core.paths
from argus import __main__ as cli_entry

REPO_ROOT = Path(argus.__file__).resolve().parents[1]


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + existing if existing else "")
    return env


def test_the_alias_package_is_exactly_two_files() -> None:
    shim = REPO_ROOT / "argus_skill"
    assert sorted(p.name for p in shim.iterdir() if p.name != "__pycache__") == [
        "__init__.py", "__main__.py",
    ]


def test_legacy_import_name_is_the_same_module_object() -> None:
    import argus_skill
    import argus_skill.core.paths as legacy_paths

    assert argus_skill is argus
    assert sys.modules["argus_skill"] is argus
    assert legacy_paths is argus.core.paths
    assert sys.modules["argus_skill.core.paths"] is argus.core.paths
    # The canonical module keeps its own spec: nothing about it says argus_skill.
    assert argus.core.paths.__spec__.name == "argus.core.paths"
    assert type(sys.meta_path[0]).__name__ == "ArgusSkillAliasFinder"


def test_patching_through_the_legacy_name_patches_the_runtime(monkeypatch) -> None:
    monkeypatch.setattr("argus_skill.core.paths.global_root", lambda: Path("/patched"))

    assert argus.core.paths.global_root() == Path("/patched")


def test_a_legacy_submodule_imported_first_is_still_the_canonical_module() -> None:
    """Order must not matter: importing the old name first cannot create a second copy."""
    code = (
        "import argus_skill.tools.pdf_chat as legacy, sys; "
        "import argus.tools.pdf_chat as canonical; "
        "assert legacy is canonical, (legacy, canonical); "
        "assert sys.modules['argus_skill.tools.pdf_chat'] is canonical; "
        "print(canonical.__spec__.name)"
    )
    probe = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, env=_subprocess_env(),
        capture_output=True, text=True, timeout=120,
    )

    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "argus.tools.pdf_chat"


def test_legacy_import_emits_one_deprecation_warning() -> None:
    probe = subprocess.run(
        [sys.executable, "-W", "always", "-c", "import argus_skill, argus_skill.core.paths"],
        cwd=REPO_ROOT, env=_subprocess_env(), capture_output=True, text=True, timeout=120,
    )

    assert probe.returncode == 0, probe.stderr
    assert probe.stderr.count("renamed to 'argus'") == 1
    assert "DeprecationWarning" in probe.stderr


@pytest.mark.parametrize(
    ("module", "arguments", "expected"),
    [
        ("argus_skill", ["--version"], f"argus {argus.__version__}"),
        ("argus_skill.tools.subagent", ["--help"], "usage: subagent"),
        ("argus", ["--version"], f"argus {argus.__version__}"),
    ],
)
def test_python_dash_m_runs_under_either_package_name(module: str, arguments: list[str], expected: str) -> None:
    probe = subprocess.run(
        [sys.executable, "-m", module, *arguments], cwd=REPO_ROOT, env=_subprocess_env(),
        capture_output=True, text=True, timeout=180,
    )

    assert probe.returncode == 0, probe.stderr
    assert expected in probe.stdout


def test_python_dash_m_argus_is_the_cli_not_the_cockpit() -> None:
    """``python -m argus`` never execs Node; only the ``argus`` console script does."""
    probe = subprocess.run(
        [sys.executable, "-m", "argus", "--help"], cwd=REPO_ROOT, env=_subprocess_env(),
        capture_output=True, text=True, timeout=180,
    )

    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.startswith("usage: argus")
    assert "Ink TUI" not in probe.stderr


def test_argus_skill_console_script_announces_the_rename_once(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["/opt/venv/bin/argus-skill", "--version"])
    with pytest.raises(SystemExit) as exit_info:
        cli_entry.main(["--version"])
    captured = capsys.readouterr()

    assert exit_info.value.code == 0
    assert captured.out == f"argus {argus.__version__}\n"
    assert captured.err == cli_entry.LEGACY_COMMAND_NOTICE
    assert captured.err.startswith("argus-skill: this command is now `argus`")


@pytest.mark.parametrize("argv0", ["/opt/venv/bin/argus", "argus/__main__.py", "C:\\venv\\Scripts\\argus.exe"])
def test_the_current_entry_points_do_not_print_the_rename_notice(monkeypatch, capsys, argv0: str) -> None:
    monkeypatch.setattr(sys, "argv", [argv0, "--version"])
    with pytest.raises(SystemExit):
        cli_entry.main(["--version"])

    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("argv0", ["argus-skill", "argus-skill.exe", "argus-skill-script.py", "/x/ARGUS-SKILL.EXE"])
def test_legacy_launcher_spellings_are_recognised(argv0: str) -> None:
    assert cli_entry._invoked_as_legacy_command(argv0) is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
@pytest.mark.parametrize("entry_module", ["argus.team.teammate_entry", "argus_skill.team.teammate_entry"])
def test_teammate_process_groups_are_recognised_under_either_spelling(entry_module: str) -> None:
    """``daemon.state`` terminates teammate process groups it recognises by argv token."""
    from argus.daemon.state import _teammate_process_group_ids

    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(30)", entry_module, "--root", "/tmp/x"],
        start_new_session=True,
    )
    try:
        for _ in range(100):
            if _teammate_process_group_ids([process.pid]):
                break
            time.sleep(0.05)
        assert _teammate_process_group_ids([process.pid]) == (os.getpgid(process.pid),)
    finally:
        process.kill()
        process.wait()
    assert _teammate_process_group_ids([process.pid]) == ()
