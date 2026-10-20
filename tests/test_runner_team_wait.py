"""The lead waits for its runtime-owned team instead of polling it with model turns."""
from __future__ import annotations

import json
import time
from pathlib import Path

import argus.team  # noqa: F401 — registers the team projection with the external-work scan
from argus.engineer import round_signals, runner
from argus.engineer.round_config import SupervisedConfig
from argus.engineer.round_state import RoundLoopState
from argus.engineer.round_waits import RoundWaitsMixin
from argus.team import pool, registry, task_board

TEAM = "research-idea-pipeline-v8-g1"
WORK = f"team:{TEAM}"


def _portfolio(project: Path, *states: str) -> Path:
    root = project / ".argus" / "teams" / TEAM
    task_board.form(root, [
        {"task_id": f"{TEAM}-route-{i:02d}", "title": "r", "objective": "o", "acceptance_check": "c"}
        for i in range(1, len(states) + 1)
    ])
    # A running task's own heartbeat is what shows its teammate is alive.
    now = time.time()
    for i, state in enumerate(states, 1):
        extra = {"owner": f"w{i}", "claim_ts": now, "heartbeat_ts": now} if state == "running" else {}
        task_board._mutate(root, f"{TEAM}-route-{i:02d}", state=state, **extra)
    pool.update(root, width=1, state="running")
    registry.write_marker(project, team_id=TEAM, team_root=root, cwd=project, now=now, owner="runtime")
    return root


def _wait(tmp_path: Path, **kwargs):
    return RoundWaitsMixin()._handle_runtime_team_wait(
        supervised_config=SupervisedConfig(max_rounds=4),
        workdir=tmp_path, state=kwargs.pop("state", RoundLoopState()),
    )


def test_no_runtime_team_means_the_round_proceeds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_TEAM_TASK_ID", raising=False)
    assert _wait(tmp_path).action == "proceed"


def test_running_team_is_waited_on_without_an_engineer_turn(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_TEAM_TASK_ID", raising=False)
    monkeypatch.setenv("ARGUS_EXTERNAL_WAIT_HOLD_MAX_SECONDS", "0")
    _portfolio(tmp_path, "running", "pending")
    calls: list[str] = []
    monkeypatch.setattr(
        round_signals, "wait_for_external_work_cadence",
        lambda workdir, work_id: (calls.append(work_id) or ("cadence_elapsed", 300.0)),
    )
    state = RoundLoopState()
    before = state.last_decision_progress_at
    control = _wait(tmp_path, state=state)
    assert calls == [WORK]
    assert control.action == "return"
    status, rounds, final_message, reason, _thread = control.terminal
    assert status == "paused_external_work" and rounds == []
    assert json.loads(final_message) == {"wait_for": "external_work", "wait_id": WORK}
    assert "released the mission slot" in reason
    assert state.last_decision_progress_at == before + 300.0


def test_lead_keeps_waiting_in_process_within_its_budget(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_TEAM_TASK_ID", raising=False)
    monkeypatch.setenv("ARGUS_EXTERNAL_WAIT_HOLD_MAX_SECONDS", "3600")
    root = _portfolio(tmp_path, "running")
    cadences: list[str] = []

    def cadence(workdir, work_id):
        cadences.append(work_id)
        if len(cadences) == 3:  # the worker finishes during the third cadence
            task_board._mutate(root, f"{TEAM}-route-01", state="done")
            return ("terminal", 40.0)
        return ("cadence_elapsed", 300.0)

    monkeypatch.setattr(round_signals, "wait_for_external_work_cadence", cadence)
    state = RoundLoopState()
    before = state.last_decision_progress_at
    control = _wait(tmp_path, state=state)
    assert cadences == [WORK] * 3
    assert state.last_decision_progress_at == before + 640.0
    assert control.action == "return"
    status, _rounds, final_message, reason, _thread = control.terminal
    assert status == "paused_external_work" and "finished" in reason
    assert json.loads(final_message)["wait_id"] == WORK


def test_failed_worker_hands_the_round_to_the_engineer_with_context(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_TEAM_TASK_ID", raising=False)
    root = _portfolio(tmp_path, "running")

    def wait(**kw):
        task_board._mutate(root, f"{TEAM}-route-01", state="failed", reason="provider refused")
        return ("needs_attention", 12.0)

    monkeypatch.setattr(runner, "_run_external_work_wait", wait)
    state = RoundLoopState()
    control = _wait(tmp_path, state=state)
    assert control.action == "proceed"
    assert "## Team follow-up" in state.pending_external_work_followup
    assert "provider refused" in state.pending_external_work_followup
    assert "do not resize the pool" in state.pending_external_work_followup


def test_daemon_stop_during_the_wait_pauses_for_shutdown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_TEAM_TASK_ID", raising=False)
    _portfolio(tmp_path, "running")
    monkeypatch.setattr(runner, "_run_external_work_wait", lambda **kw: ("stop_requested", 5.0))
    control = _wait(tmp_path)
    assert control.action == "return" and control.terminal[0] == "paused_daemon_shutdown"


def test_a_worker_never_waits_for_its_own_team(tmp_path: Path, monkeypatch) -> None:
    _portfolio(tmp_path, "running")
    monkeypatch.setenv("ARGUS_SKILL_TEAM_TASK_ID", f"{TEAM}-route-01")
    monkeypatch.setattr(runner, "_run_external_work_wait", lambda **kw: (_ for _ in ()).throw(AssertionError("waited")))
    assert _wait(tmp_path).action == "proceed"


def test_finished_team_at_round_start_lets_the_engineer_settle_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_TEAM_TASK_ID", raising=False)
    _portfolio(tmp_path, "done", "done")
    monkeypatch.setattr(runner, "_run_external_work_wait", lambda **kw: (_ for _ in ()).throw(AssertionError("waited")))
    assert _wait(tmp_path).action == "proceed"
