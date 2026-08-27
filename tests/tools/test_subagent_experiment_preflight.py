from __future__ import annotations

import json
import time
from pathlib import Path

from argus_skill.tools.subagent._experiment_preflight import (
    experiment_launch_preflight,
    release_experiment_launch_claim,
)


def test_preflight_rejects_missing_local_input(tmp_path: Path) -> None:
    rejected, concern = experiment_launch_preflight(
        task_id="missing-input",
        command="python worker.py --tasks benchmarks/missing.jsonl",
        cwd=str(tmp_path),
        run_dir=None,
    )

    assert rejected is True
    assert "required --tasks input does not exist" in concern


def test_preflight_rejects_existing_stop_file(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "STOP").write_text("", encoding="utf-8")

    rejected, concern = experiment_launch_preflight(
        task_id="stopped",
        command="python -c 'print(1)'",
        cwd=str(tmp_path),
        run_dir=str(run_dir),
    )

    assert rejected is True
    assert "STOP file is present" in concern


def test_preflight_reconciles_ownerless_running_status(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    status_path = run_dir / "status.json"
    status_path.write_text(
        json.dumps({"state": "running", "updated_at": time.time() - 100}),
        encoding="utf-8",
    )

    rejected, concern = experiment_launch_preflight(
        task_id="relaunch",
        command="python -c 'print(1)'",
        cwd=str(tmp_path),
        run_dir=str(run_dir),
    )

    assert rejected is False
    assert concern == ""
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["error"] == "running status has no live owner; reconciled before relaunch"


def test_preflight_reconciles_recent_ownerless_running_status(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "status.json").write_text(
        json.dumps({"state": "running", "updated_at": time.time()}),
        encoding="utf-8",
    )

    rejected, concern = experiment_launch_preflight(
        task_id="duplicate",
        command="python -c 'print(1)'",
        cwd=str(tmp_path),
        run_dir=str(run_dir),
    )

    assert rejected is False
    assert concern == ""


def test_preflight_allows_remote_dataset_identifier(tmp_path: Path) -> None:
    rejected, concern = experiment_launch_preflight(
        task_id="remote-data",
        command="python -c 'print(1)' --data allenai/c4",
        cwd=str(tmp_path),
        run_dir=None,
    )

    assert rejected is False
    assert concern == ""


def test_preflight_understands_env_unset_option(tmp_path: Path) -> None:
    rejected, concern = experiment_launch_preflight(
        task_id="env-unset",
        command="env -u ARGUS_SKILL_VERTICAL python -c 'print(1)'",
        cwd=str(tmp_path),
        run_dir=None,
    )

    assert rejected is False
    assert concern == ""


def test_preflight_does_not_misresolve_inputs_after_shell_cd(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "tasks.jsonl").write_text("{}\n", encoding="utf-8")

    rejected, concern = experiment_launch_preflight(
        task_id="shell-cd",
        command="cd nested && python worker.py --tasks tasks.jsonl",
        cwd=str(tmp_path),
        run_dir=None,
    )

    assert rejected is False
    assert concern == ""


def test_run_directory_claim_is_atomic_across_tasks(tmp_path: Path) -> None:
    run_dir = tmp_path / "shared-run"
    first_rejected, _ = experiment_launch_preflight(
        task_id="first",
        command="python -c 'print(1)'",
        cwd=str(tmp_path),
        run_dir=str(run_dir),
    )
    second_rejected, second_concern = experiment_launch_preflight(
        task_id="second",
        command="python -c 'print(1)'",
        cwd=str(tmp_path),
        run_dir=str(run_dir),
    )

    assert first_rejected is False
    assert second_rejected is True
    assert "already claimed by task first" in second_concern

    release_experiment_launch_claim(
        task_id="first",
        cwd=str(tmp_path),
        run_dir=str(run_dir),
    )
    retry_rejected, retry_concern = experiment_launch_preflight(
        task_id="second",
        command="python -c 'print(1)'",
        cwd=str(tmp_path),
        run_dir=str(run_dir),
    )
    assert retry_rejected is False
    assert retry_concern == ""
    release_experiment_launch_claim(
        task_id="second",
        cwd=str(tmp_path),
        run_dir=str(run_dir),
    )
