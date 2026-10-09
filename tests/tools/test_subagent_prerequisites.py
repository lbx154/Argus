from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from argus.tools.subagent import _cli, _direct_run, _registry, _reporting, _supervised_run
from argus.tools.subagent._experiment_preflight import (
    PrerequisiteError,
    experiment_launch_preflight,
    resolve_prerequisites,
)


def record(task_id: str, *, state="done", exit_code=0, run_id="first", **extra) -> None:
    _registry._write_task(task_id, {
        "task_id": task_id, "run_id": run_id, "state": state,
        "exit_code": exit_code, **extra,
    })


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))


@pytest.mark.parametrize("state,code", [
    ("running", None), ("error", 2), ("timeout", None),
    ("done", None), ("done", False), ("done", 1),
])
def test_only_successful_terminal_receipts_authorize_work(state, code):
    record("prepare", state=state, exit_code=code)
    with pytest.raises(PrerequisiteError, match="did not pass"):
        resolve_prerequisites(["prepare"])


def test_prerequisite_must_exist_and_name_a_run():
    with pytest.raises(PrerequisiteError, match="no task receipt"):
        resolve_prerequisites(["absent"])
    record("prepare", run_id="")
    with pytest.raises(PrerequisiteError, match="no recorded run_id"):
        resolve_prerequisites(["prepare"])


def test_prerequisites_bind_the_successful_run_and_reject_replacement(tmp_path):
    record("prepare")
    bound = resolve_prerequisites(["prepare", "prepare"])
    assert bound == {"prepare": "first"}
    record("prepare", run_id="second")
    rejected, concern = experiment_launch_preflight(
        task_id="dependent", command="echo ready", cwd=str(tmp_path),
        run_dir=None, prerequisites=bound,
    )
    assert rejected
    assert "changed after submission" in concern


@pytest.mark.parametrize("mode", ["direct", "supervised"])
def test_failed_prerequisite_prevents_process_and_model_launch(monkeypatch, tmp_path, mode):
    record("prepare", state="error", exit_code=2)
    record("dependent", state="starting", exit_code=None,
           prerequisites={"prepare": "first"})
    runner = _direct_run if mode == "direct" else _supervised_run
    launch = Mock(side_effect=AssertionError("dependent process must not start"))
    model = Mock(side_effect=AssertionError("model must not be called"))
    monkeypatch.setattr(runner, "_launch_durable_command", launch)
    monkeypatch.setattr(runner, "acquire_for_task", launch)
    alerts = []
    monkeypatch.setattr(runner, "_alert_engineer",
                        lambda task_id, event, data: alerts.append((event, data)) or "")
    if mode == "direct":
        runner._run_direct("dependent", "echo main", "test", 10, str(tmp_path))
    else:
        monkeypatch.setattr(runner, "_run_supervisor_with_usage", model)
        monkeypatch.setattr(runner, "_persist_experiment_record", lambda *args: None)
        runner._run_supervised(
            "dependent", "echo main", "test", 10, 120, "", str(tmp_path), preflight=False,
        )
    launch.assert_not_called()
    model.assert_not_called()
    assert alerts[0][0] == "PREFLIGHT-REJECTED"
    assert alerts[0][1]["preflight"] is True
    assert alerts[0][1]["prerequisites"] == {"prepare": "first"}
    assert _registry._read_task("dependent")["state"] == "error"


@pytest.mark.parametrize("mode", ["direct", "supervised"])
def test_prerequisite_changed_during_resource_admission_is_rechecked(
    monkeypatch, tmp_path, mode,
):
    record("prepare")
    record("dependent", state="starting", exit_code=None,
           prerequisites={"prepare": "first"})
    runner = _direct_run if mode == "direct" else _supervised_run

    def acquire(*args, **kwargs):
        record("prepare", run_id="replacement")
        return None

    monkeypatch.setattr(runner, "acquire_for_task", acquire)
    launch = Mock(side_effect=AssertionError("stale prerequisite must not launch"))
    monkeypatch.setattr(runner, "_launch_durable_command", launch)
    alerts = []
    monkeypatch.setattr(runner, "_alert_engineer",
                        lambda task_id, event, data: alerts.append((event, data)) or "")
    if mode == "direct":
        runner._run_direct("dependent", "echo main", "test", 10, str(tmp_path))
    else:
        monkeypatch.setattr(runner, "_persist_experiment_record", lambda *args: None)
        runner._run_supervised(
            "dependent", "echo main", "test", 10, 120, "", str(tmp_path), preflight=False,
        )
    launch.assert_not_called()
    assert alerts[-1][0] == "PREFLIGHT-REJECTED"
    assert alerts[-1][1]["preflight"] is True
    assert "changed after submission" in alerts[-1][1]["error"]


def submit(monkeypatch, *extra):
    monkeypatch.setattr(sys, "argv", [
        "subagent", "submit", "--task-id", "dependent",
        "--command", "echo main", *extra,
    ])
    return _cli.main()


def test_cli_blocks_before_creating_a_task_or_spawning(monkeypatch, capsys):
    record("prepare", state="error", exit_code=2)
    spawn = Mock(side_effect=AssertionError("blocked submission must not spawn"))
    monkeypatch.setattr(_cli, "_spawn_windows_worker", spawn)
    if hasattr(os, "fork"):
        monkeypatch.setattr(os, "fork", spawn)
    assert submit(monkeypatch, "--depends-on", "prepare", "--no-preflight") == 1
    response = json.loads(capsys.readouterr().out)
    assert response["state"] == "blocked"
    assert "prepare" in response["error"]
    assert _registry._read_task("dependent") is None
    spawn.assert_not_called()


def test_self_dependency_is_rejected(monkeypatch, capsys):
    assert submit(monkeypatch, "--depends-on", "dependent") == 1
    assert "cannot depend on itself" in capsys.readouterr().out


@pytest.mark.parametrize("rejection", ["prerequisite", "rerun"])
def test_rejected_submission_does_not_clear_previous_stop(monkeypatch, tmp_path, rejection):
    stop = tmp_path / "STOP"
    stop.touch()
    extra = ["--run-dir", str(tmp_path), "--clear-stop"]
    if rejection == "prerequisite":
        extra.extend(["--depends-on", "absent"])
    else:
        record("dependent", command="echo main")
    assert submit(monkeypatch, *extra) == 1
    assert stop.exists()


def test_stop_removal_failure_does_not_admit_task(monkeypatch, tmp_path, capsys):
    stop = tmp_path / "STOP"
    stop.touch()
    unlink = Path.unlink

    def reject_stop(path, *args, **kwargs):
        if path == stop:
            raise PermissionError("read-only STOP")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", reject_stop)
    assert submit(monkeypatch, "--run-dir", str(tmp_path), "--clear-stop") == 1
    assert "could not clear STOP" in capsys.readouterr().out
    assert _registry._read_task("dependent") is None


@pytest.mark.parametrize("state,code", [("done", 0), ("error", 2)])
def test_identical_command_requires_reason_and_keeps_previous_record(
    monkeypatch, capsys, state, code,
):
    record("dependent", state=state, exit_code=code, command="echo main")
    before = _registry._read_task("dependent")
    assert submit(monkeypatch, "--rerun-reason", " ") == 1
    response = json.loads(capsys.readouterr().out)
    assert "--rerun-reason" in response["error"]
    assert _registry._read_task("dependent") == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX submission parent branch")
def test_submission_records_reason_and_prerequisite_without_new_identifiers(monkeypatch, capsys):
    record("prepare")
    record("dependent", state="error", exit_code=2, command="echo main")
    monkeypatch.setattr(os, "fork", lambda: 99999999)
    assert submit(
        monkeypatch, "--depends-on", "prepare",
        "--rerun-reason", "Fixed the missing input described by the previous failure",
    ) == 0
    response = json.loads(capsys.readouterr().out)
    current = _registry._read_task("dependent")
    assert current["run_id"] == response["run_id"]
    assert current["previous_run_id"] == "first"
    assert current["prerequisites"] == {"prepare": "first"}
    assert current["rerun_reason"].startswith("Fixed the missing input")
    _registry._write_task("dependent", {
        "task_id": "dependent", "run_id": current["run_id"], "state": "done", "exit_code": 0,
    })
    final = _registry._read_task("dependent")
    assert final["prerequisites"] == current["prerequisites"]
    report = _reporting._build_report("dependent", "COMPLETED", final)
    assert current["rerun_reason"] in report
    assert "prepare" in report
    record("dependent", run_id="unrelated-new-run")
    new = _registry._read_task("dependent")
    assert "prerequisites" not in new
    assert "previous_run_id" not in new


def test_shared_role_rule_covers_both_shells(monkeypatch):
    from argus.roles.prompts import engineer

    for native in ("", "Windows PowerShell"):
        monkeypatch.setattr(engineer, "native_shell_contract", lambda: native)
        rule = engineer._long_experiment_rule()
        assert "--depends-on" in rule
        assert "--rerun-reason" in rule
        assert "A readiness pass is not a scientific result." in rule


@pytest.mark.parametrize("round_index", [1, 2])
def test_independent_evidence_rule_applies_from_first_review(round_index):
    from argus.reviewer import Reviewer
    from argus.roles.prompts.reviewer import _EXPERIMENT_EVIDENCE_RULE

    prompt = Reviewer(runner=None, skill_store=None)._build_prompt(
        objective="Verify the experiment",
        operator_messages=[],
        planner_review_instruction="",
        round_index=round_index,
        session_id=None,
        main_summary="Readiness passed",
        main_error=None,
        prior_checkpoint={},
        prev_review_summary="Missing evidence" if round_index > 1 else "",
    )
    assert prompt.count(_EXPERIMENT_EVIDENCE_RULE) == 1
