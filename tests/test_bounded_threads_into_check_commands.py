from __future__ import annotations

from pathlib import Path

from argus_skill.apps._life_repl import _repl_check_commands_for_open_ended
from argus_skill.daemon.life_worker import (
    LifeWorkerConfig,
    _apply_bounded_to_check_commands,
    _runner_namespace,
)


def test_bounded_appends_bounded_flag_to_stage_check(tmp_path: Path):
    cfg = LifeWorkerConfig(
        life_dir=tmp_path / "life",
        project_workdir=tmp_path,
        backend="memory",
        continuous_open_ended=False,
    )

    ns = _runner_namespace(cfg)

    stage_checks = [cmd for cmd in ns.check_commands if "stage_check" in cmd]
    assert stage_checks
    assert all("--bounded" in cmd for cmd in stage_checks)


def test_unbounded_check_command_has_no_bounded_flag(tmp_path: Path):
    cfg = LifeWorkerConfig(
        life_dir=tmp_path / "life",
        project_workdir=tmp_path,
        backend="memory",
        continuous_open_ended=True,
    )

    ns = _runner_namespace(cfg)

    stage_checks = [cmd for cmd in ns.check_commands if "stage_check" in cmd]
    assert stage_checks
    assert all("--bounded" not in cmd for cmd in stage_checks)


def test_custom_non_stage_check_command_is_unchanged():
    commands = ["pytest -q", "python -m argus_skill.tools.stage_check --project-root ."]

    out = _apply_bounded_to_check_commands(commands, bounded=True)

    assert out[0] == "pytest -q"
    assert out[1].endswith("--bounded")


def test_existing_bounded_flag_not_duplicated():
    commands = ["python -m argus_skill.tools.stage_check --project-root . --bounded"]

    out = _apply_bounded_to_check_commands(commands, bounded=True)

    assert out == commands


def test_repl_bounded_adds_flag_to_stage_check_command():
    out = _repl_check_commands_for_open_ended(
        ["python -m argus_skill.tools.stage_check --project-root ."],
        open_ended=False,
    )
    assert out == ["python -m argus_skill.tools.stage_check --project-root . --bounded"]


def test_repl_open_ended_keeps_strict_stage_check_command():
    out = _repl_check_commands_for_open_ended(
        ["python -m argus_skill.tools.stage_check --project-root ."],
        open_ended=True,
    )
    assert out == ["python -m argus_skill.tools.stage_check --project-root ."]
