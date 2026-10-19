"""Liveness of concurrent handoffs: every wait has a deadline and a fallback.

Regression tests for the concurrency audit: blue/green daemon handoff, stale
external-work heartbeats, the team-task orphan budget, subagent terminal-state
names, the Curator's stop/tick race, and discussion-blocker scoping.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import argus.daemon._life_worker_run as run_mod
import argus.daemon.handoff as handoff_mod
import argus.daemon.life_worker as life_worker_mod
from argus.core import process_stop
from argus.daemon.config import config_payload as _config_payload
from argus.daemon.life_worker import LifeWorker, LifeWorkerConfig
from argus.engineer.external_work import ExternalWorkState, inspect_external_work
from argus.life.memory import BacklogItem, LifeMemory
from argus.team import curator as cur
from argus.team import external_work as team_work
from argus.team import pool, registry, task_board
from argus.tools import subagent as sa

# ---------------------------------------------------------------------------
# R1 — blue/green handoff leaves exactly one live daemon
# ---------------------------------------------------------------------------


def test_handoff_gate_drains_helpers_and_lead_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = LifeWorkerConfig(life_dir=tmp_path / "life", backend="memory")
    cfg.life_dir.mkdir(parents=True)
    deployed = tmp_path / "deployed"
    deployed.mkdir()
    handoff_mod.request_deployment_handoff(
        cfg.life_dir / "maintenance" / "receipts", deployed,
    )
    monkeypatch.setattr(
        life_worker_mod, "_spawn_handoff_candidate", lambda *_a, **_k: 4242,
    )
    monkeypatch.setattr(
        "argus.core.runtime_identity.source_root", lambda: tmp_path / "prior",
    )
    worker = LifeWorker(cfg)

    assert worker._deployment_handoff_gate() == "daemon_handoff"

    # Helper missions run on ``_mission_stop``; the lead's team wait polls the
    # process-wide flag. Both must stop, or the lock outlives the successor.
    assert worker._stop.is_set()
    assert worker._mission_stop.is_set()
    assert process_stop.stop_requested()
    assert worker._handoff_successor_pid == 4242
    # A handoff is not an operator clock-out: continuous mode must survive.
    assert worker._operator_stop_requested is False


def _handoff_child_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, incumbent_pid: int,
) -> None:
    cfg = LifeWorkerConfig(life_dir=tmp_path / "life", backend="memory")
    cfg.life_dir.mkdir(parents=True)
    config_path = tmp_path / "handoff-config.json"
    config_path.write_text(json.dumps({"config": _config_payload(cfg)}), encoding="utf-8")
    monkeypatch.setenv(life_worker_mod._HANDOFF_CONFIG_ENV, str(config_path))
    monkeypatch.setenv(life_worker_mod._HANDOFF_READY_ENV, str(tmp_path / "ready.json"))
    monkeypatch.setenv(life_worker_mod._HANDOFF_TOKEN_ENV, "token-1")
    monkeypatch.setenv(handoff_mod._HANDOFF_INCUMBENT_PID_ENV, str(incumbent_pid))
    monkeypatch.delenv(handoff_mod._HANDOFF_ROLLBACK_SOURCE_ENV, raising=False)
    monkeypatch.setattr(LifeWorker, "run_forever", lambda _self: 0)


class _FakeLock:
    def release(self) -> None:
        return


def _operator_alerts(life_dir: Path) -> list[dict[str, Any]]:
    path = life_dir / "events.jsonl"
    if not path.is_file():
        return []
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [event for event in events if event.get("type") == "operator_alert"]


@pytest.mark.parametrize("incumbent_env", [True, False])
def test_handoff_candidate_waits_as_long_as_the_incumbent_lives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, incumbent_env: bool,
) -> None:
    _handoff_child_env(tmp_path, monkeypatch, incumbent_pid=4321)
    if not incumbent_env:
        # An incumbent from an earlier release names itself only as the
        # daemon lock's holder; the first holder seen is that incumbent.
        monkeypatch.delenv(handoff_mod._HANDOFF_INCUMBENT_PID_ENV)
    attempts: list[float] = []
    # Each check sees ten more minutes pass: a fixed drain cap would have
    # given up long before this incumbent finished.
    clock = iter(range(0, 10**9, 600))
    monkeypatch.setattr(
        handoff_mod,
        "time",
        SimpleNamespace(monotonic=lambda: float(next(clock)), time=time.time, sleep=time.sleep),
    )

    def fake_acquire(pid_path: Path, timeout: float) -> _FakeLock:
        attempts.append(timeout)
        if len(attempts) < 25:  # the incumbent is still draining
            raise life_worker_mod.DaemonAlreadyRunning(4321, pid_path)
        return _FakeLock()

    monkeypatch.setattr(life_worker_mod, "_acquire_daemon_lock_with_timeout", fake_acquire)
    monkeypatch.setattr(handoff_mod, "is_pid_running", lambda pid: pid == 4321)

    assert life_worker_mod.run_handoff_child() == 0
    assert attempts == [60.0] * 25
    assert _operator_alerts(tmp_path / "life") == []


@pytest.mark.parametrize(
    ("holder", "incumbent_alive"),
    # Another process took the lock (the incumbent died, or never held it),
    # or the incumbent itself is gone: either way this handoff is over.
    [(999, True), (999, False), (4321, False)],
)
def test_handoff_candidate_gives_up_only_when_the_lock_is_not_the_incumbents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    holder: int,
    incumbent_alive: bool,
) -> None:
    _handoff_child_env(tmp_path, monkeypatch, incumbent_pid=4321)
    attempts: list[float] = []

    def fake_acquire(pid_path: Path, timeout: float) -> Any:
        attempts.append(timeout)
        raise life_worker_mod.DaemonAlreadyRunning(holder, pid_path)

    monkeypatch.setattr(life_worker_mod, "_acquire_daemon_lock_with_timeout", fake_acquire)
    monkeypatch.setattr(handoff_mod, "is_pid_running", lambda _pid: incumbent_alive)

    assert life_worker_mod.run_handoff_child() == 2
    assert attempts == [60.0]
    # The dropped handoff reaches the operator's timeline, not only a log.
    alerts = _operator_alerts(tmp_path / "life")
    assert [alert["reason"] for alert in alerts] == ["daemon_handoff_lock_taken"]
    assert alerts[0]["operator_alert"] is True
    assert f"pid {holder}" in alerts[0]["text"]


@pytest.mark.parametrize(
    ("successor_alive", "operator_stop", "expect_replacement"),
    [(False, False, True), (True, False, False), (False, True, False)],
)
def test_shutdown_replaces_a_successor_that_never_took_over(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    successor_alive: bool,
    operator_stop: bool,
    expect_replacement: bool,
) -> None:
    prior = tmp_path / "prior-source"
    prior.mkdir()
    spawned: list[dict[str, Any]] = []
    monkeypatch.setattr(run_mod, "is_pid_running", lambda _pid: successor_alive)
    monkeypatch.setattr(
        life_worker_mod,
        "_spawn_handoff_candidate",
        lambda config, **kwargs: spawned.append(kwargs) or 777,
    )
    monkeypatch.setattr("argus.core.runtime_identity.source_root", lambda: prior)
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory"))
    worker._handoff_successor_pid = 4242
    worker._operator_stop_requested = operator_stop
    monkeypatch.setattr(worker, "_quiesce_continuous_on_operator_stop", lambda *_a: None)

    assert worker._rf_shutdown(SimpleNamespace(runtime_root=tmp_path)) == 0

    if expect_replacement:
        # The current runtime restarts in the lost successor's place; it is a
        # plain restart, so it carries no rollback of its own.
        assert len(spawned) == 1
        assert spawned[0]["candidate_source_root"] == prior
        assert "rollback_source_root" not in spawned[0]
        # The deployment it carried is dropped, and the operator is told.
        alerts = _operator_alerts(tmp_path)
        assert [alert["reason"] for alert in alerts] == ["daemon_handoff_successor_lost"]
        assert "pid 4242" in alerts[0]["text"]
    else:
        assert spawned == []
        assert _operator_alerts(tmp_path) == []


def test_shutdown_tells_the_operator_when_no_daemon_is_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_mod, "is_pid_running", lambda _pid: False)
    monkeypatch.setattr(life_worker_mod, "_spawn_handoff_candidate", lambda *_a, **_k: 0)
    monkeypatch.setattr("argus.core.runtime_identity.source_root", lambda: tmp_path)
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory"))
    worker._handoff_successor_pid = 4242

    assert worker._rf_shutdown(SimpleNamespace(runtime_root=tmp_path)) == 0

    alerts = _operator_alerts(tmp_path)
    assert [alert["reason"] for alert in alerts] == ["daemon_handoff_replacement_failed"]
    assert "no daemon is running" in alerts[0]["text"]


def test_handoff_gate_tells_the_operator_when_the_deployment_never_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = LifeWorkerConfig(life_dir=tmp_path / "life", backend="memory")
    cfg.life_dir.mkdir(parents=True)
    deployed = tmp_path / "deployed"
    deployed.mkdir()
    handoff_mod.request_deployment_handoff(
        cfg.life_dir / "maintenance" / "receipts", deployed,
    )
    monkeypatch.setattr(life_worker_mod, "_spawn_handoff_candidate", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        "argus.core.runtime_identity.source_root", lambda: tmp_path / "prior",
    )
    worker = LifeWorker(cfg)

    assert worker._deployment_handoff_gate() == ""

    assert not worker._stop.is_set()
    alerts = _operator_alerts(cfg.life_dir)
    assert [alert["reason"] for alert in alerts] == ["daemon_handoff_standby_failed"]
    assert str(deployed.resolve()) in alerts[0]["text"]


def test_rollback_that_cannot_start_tells_the_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _handoff_child_env(tmp_path, monkeypatch, incumbent_pid=4321)
    monkeypatch.setenv(handoff_mod._HANDOFF_ROLLBACK_SOURCE_ENV, str(tmp_path))
    monkeypatch.setattr(LifeWorker, "run_forever", lambda _self: 1)
    monkeypatch.setattr(
        life_worker_mod, "_acquire_daemon_lock_with_timeout", lambda *_a: _FakeLock(),
    )
    monkeypatch.setattr(handoff_mod, "_spawn_handoff_candidate", lambda *_a, **_k: 0)

    assert life_worker_mod.run_handoff_child() == 1

    alerts = _operator_alerts(tmp_path / "life")
    assert [alert["reason"] for alert in alerts] == ["daemon_handoff_rollback_failed"]


# ---------------------------------------------------------------------------
# R2 — a stale heartbeat wakes the waiting parent with the fact
# ---------------------------------------------------------------------------


def _subagent(root: Path, task_id: str, **fields: object) -> None:
    directory = root / ".argus_subagents"
    directory.mkdir(exist_ok=True)
    record = {
        "task_id": task_id,
        "state": "running",
        "worker_pid": os.getpid(),
        "last_supervisor_health": "healthy",
        "last_supervisor_decision": "continue",
        "monitor_interval": 120,
        **fields,
    }
    (directory / f"{task_id}.json").write_text(json.dumps(record), encoding="utf-8")


def test_wedged_supervised_subagent_with_live_pid_is_stalled(tmp_path: Path) -> None:
    now = 100_000.0
    _subagent(tmp_path, "wedged", mode="supervised", heartbeat_at=now - 3_600)
    _subagent(tmp_path, "fresh", mode="supervised", heartbeat_at=now - 60)

    wedged = inspect_external_work(tmp_path, "wedged", now=now)
    fresh = inspect_external_work(tmp_path, "fresh", now=now)

    assert wedged is not None and wedged.state is ExternalWorkState.STALLED
    assert not wedged.waitable
    assert "heartbeat is stale" in wedged.reason
    assert fresh is not None and fresh.state is ExternalWorkState.RUNNING_HEALTHY


def test_stale_resource_wait_is_stalled_but_long_direct_run_is_not(tmp_path: Path) -> None:
    now = time.time() + 4 * 3600.0
    _subagent(
        tmp_path, "queued", mode="direct", state="waiting_resource",
        heartbeat_at=now - 3_600,
    )
    # A direct worker writes its record once and then waits on the command:
    # an old record is a long run, not a wedged owner.
    _subagent(tmp_path, "training", mode="direct")

    queued = inspect_external_work(tmp_path, "queued", now=now)
    training = inspect_external_work(tmp_path, "training", now=now)

    assert queued is not None and queued.state is ExternalWorkState.STALLED
    assert training is not None and training.state is ExternalWorkState.RUNNING_HEALTHY


def _team(project: Path, *states: str, team_id: str = "t") -> Path:
    root = project / ".argus" / "teams" / team_id
    task_board.form(root, [
        {"task_id": f"{team_id}-{index}", "objective": "o"}
        for index in range(1, len(states) + 1)
    ])
    for index, state in enumerate(states, 1):
        changes: dict[str, Any] = {"state": state}
        if state in {"claimed", "running"}:
            changes.update(owner=f"w{index}", claim_ts=1_000.0, heartbeat_ts=1_500.0)
        if state == "waiting_external":
            changes.update(
                external_wait={"kind": "subagent", "work_id": "job", "workdir": str(project)},
                finished_ts=1_500.0,
            )
        task_board._mutate(root, f"{team_id}-{index}", **changes)
    pool.update(root, width=1, state="running")
    registry.write_marker(project, team_id=team_id, team_root=root, cwd=project, now=900.0)
    return root


def test_team_with_only_waiting_tasks_is_not_terminal(tmp_path: Path) -> None:
    _team(tmp_path, "waiting_external", "done")
    status = inspect_external_work(tmp_path, "team:t", now=time.time())

    assert status is not None
    assert status.state is ExternalWorkState.RUNNING_HEALTHY
    assert "1 waiting on external work" in status.description


def test_team_board_stale_past_its_limit_is_stalled(tmp_path: Path) -> None:
    _team(tmp_path, "running", "pending")
    later = time.time() + team_work._TEAM_STALE_AFTER_SECONDS + 3600.0

    status = inspect_external_work(tmp_path, "team:t", now=later)

    assert status is not None and status.state is ExternalWorkState.STALLED
    assert "has not changed" in status.reason
    # A parked task's own work has its own staleness; the board does not judge it.
    _team(tmp_path, "waiting_external", "done", team_id="p")
    parked = inspect_external_work(tmp_path, "team:p", now=later)
    assert parked is not None and parked.state is ExternalWorkState.RUNNING_HEALTHY
    # Nor does a parked or finished sibling vouch for a wedged running task,
    # however fresh its own stamps are.
    root = _team(tmp_path, "running", "waiting_external", "done", team_id="w")
    task_board._mutate(root, "w-2", heartbeat_ts=later, finished_ts=later)
    task_board._mutate(root, "w-3", finished_ts=later)
    masked = inspect_external_work(tmp_path, "team:w", now=later)
    assert masked is not None and masked.state is ExternalWorkState.STALLED
    # Pending work queues for the Curator (its in-flight cap, a daemon
    # restart); nothing claimed it, so nothing is wedged.
    _team(tmp_path, "pending", "pending", team_id="q")
    queued = inspect_external_work(tmp_path, "team:q", now=later)
    assert queued is not None and queued.state is ExternalWorkState.RUNNING_HEALTHY


# ---------------------------------------------------------------------------
# R4 — a teammate that keeps dying exhausts the mission orphan budget
# ---------------------------------------------------------------------------


def test_team_task_fails_after_the_orphan_budget(tmp_path: Path) -> None:
    task_board.form(tmp_path, [{"task_id": "a", "objective": "x"}])
    now = 0.0
    for orphaning in range(1, task_board.ORPHAN_RETRY_BUDGET + 1):
        now += 1_000.0
        assert task_board.claim_top(tmp_path, f"w{orphaning}", now=now) is not None
        assert task_board.reassign_stale(tmp_path, ttl=180.0, now=now + 500.0) == ["a"]
        task = task_board.snapshot(tmp_path)[0]
        assert task["state"] == "pending" and task["orphan_retries"] == orphaning

    task_board.claim_top(tmp_path, "w-last", now=now + 1_000.0)
    assert task_board.reassign_stale(tmp_path, ttl=180.0, now=now + 1_500.0) == ["a"]

    task = task_board.snapshot(tmp_path)[0]
    assert task["state"] == "failed"
    assert "orphan budget 3" in task["reason"]
    assert task_board.claim_top(tmp_path, "w-again", now=now + 2_000.0) is None


def test_clean_stop_requeue_and_lead_retry_do_not_spend_the_orphan_budget(
    tmp_path: Path,
) -> None:
    task_board.form(tmp_path, [{"task_id": "a", "objective": "x"}])
    for round_index in range(5):
        task_board.claim_top(tmp_path, f"w{round_index}", now=float(round_index))
        task_board.reassign_stale(
            tmp_path, ttl=-1.0, now=float(round_index), orphan_budget=None,
        )
    task = task_board.snapshot(tmp_path)[0]
    assert task["state"] == "pending" and task["orphan_retries"] == 0

    task_board._mutate(tmp_path, "a", state="failed", orphan_retries=4)
    assert task_board.retry_terminal(tmp_path, "a", reason="redo")
    assert task_board.snapshot(tmp_path)[0]["orphan_retries"] == 0


# ---------------------------------------------------------------------------
# R5 — the daemon recognizes the terminal states subagent writers write
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("terminal_state", ["early_stopped", "crashed"])
def test_parent_is_requeued_after_owned_subagent_settles(
    tmp_path: Path, terminal_state: str,
) -> None:
    project = tmp_path / "project"
    subagents = project / ".argus_subagents"
    subagents.mkdir(parents=True)
    memory = LifeMemory.open(tmp_path)
    memory.init()
    item = BacklogItem.new(title="stalled", objective="collect the result")
    memory.backlog.add(item)
    memory.backlog.mark_running(item.id)
    (subagents / "job.json").write_text(json.dumps({
        "task_id": "job",
        "state": terminal_state,
        "owner_mission_id": item.id,
        "completed_at": time.time(),
    }), encoding="utf-8")
    worker = LifeWorker(LifeWorkerConfig(
        life_dir=tmp_path, backend="memory", project_workdir=project,
    ))
    dead_executor = threading.Thread(target=lambda: None)
    dead_executor.start()
    dead_executor.join()
    worker._supervisor_execution_threads = {"primary": dead_executor}
    state = SimpleNamespace(
        cfg=worker.config,
        mem=memory,
        runtime_root=tmp_path,
        sink=SimpleNamespace(handle_event=lambda _event: None),
    )

    assert worker._fail_stalled_running_items(state) == [item.id]

    stored = next(row for row in memory.backlog.active() if row.id == item.id)
    assert stored.status == "pending"
    assert stored.outcome["external_wait"]["work_id"] == "job"


# ---------------------------------------------------------------------------
# R6 — nothing is spawned once the Curator is stopping
# ---------------------------------------------------------------------------


class _FakeProc:
    pid = 91_000

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_curator_spawns_nothing_after_stop_is_requested(tmp_path: Path) -> None:
    made: list[str] = []
    curator = cur.Curator(
        project_root=tmp_path,
        make_proc=lambda _root, member_id, *_a: made.append(member_id) or _FakeProc(),
    )
    curator._stop.set()

    assert curator._spawn_tracked(tmp_path, member_id="w1", task_id="a", cwd=tmp_path) == 0
    assert made == [] and curator._children == {}


def test_curator_adopts_nothing_after_stop_is_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "team"
    cur.roster.add_member(root, {"id": "w1", "pid": 4242, "cwd": str(tmp_path),
                                 "task_id": "a", "status": "running"})
    monkeypatch.setattr(cur, "_pid_is_teammate", lambda pid, _mid, _root=None: pid == 4242)
    curator = cur.Curator(project_root=tmp_path, make_proc=lambda *_a: _FakeProc())
    # stop() already tore ``_children`` down; an adoption now would leave the
    # teammate owned by nobody, so the next daemon's Curator adopts it instead.
    curator._stop.set()

    assert curator._adopt_orphans(root, now=100.0) == []
    assert curator._children == {}


def test_claim_raced_by_stop_is_handed_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "team"
    task_board.form(root, [{"task_id": "a", "objective": "x"}])
    made: list[str] = []
    curator = cur.Curator(
        project_root=tmp_path,
        make_proc=lambda _root, member_id, *_a: made.append(member_id) or _FakeProc(),
    )
    real_claim = task_board.claim_top

    def claim_then_stop(*args: Any, **kwargs: Any) -> Any:
        claimed = real_claim(*args, **kwargs)
        curator._stop.set()  # stop() lands while the tick holds the claim
        return claimed

    monkeypatch.setattr(cur.task_board, "claim_top", claim_then_stop)

    result = curator._refill(root, width=2, cwd=tmp_path, now=100.0)

    assert result["spawned"] == [] and made == []
    task = task_board.snapshot(root)[0]
    assert task["state"] == "pending" and task["owner"] == ""
    assert float(task["retry_after_ts"]) <= 100.0


# ---------------------------------------------------------------------------
# R3 — a parked discussion blocks only its owner's submits
# ---------------------------------------------------------------------------


def _park(task_id: str, owner_team_task_id: str) -> None:
    sa._write_task(task_id, {
        "task_id": task_id,
        "state": "discussing",
        "worker_pid": os.getpid(),
        "last_heartbeat": time.time(),
        "owner_team_task_id": owner_team_task_id,
    })


def test_teammate_discussion_does_not_block_the_lead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)  # REGISTRY_DIR is relative to cwd
    _park("route-probe", "team::route-1")
    _park("lead-probe", "")

    monkeypatch.delenv("ARGUS_SKILL_TEAM_TASK_ID", raising=False)
    assert [b["task_id"] for b in sa._open_discussion_blockers()] == ["lead-probe"]

    monkeypatch.setenv("ARGUS_SKILL_TEAM_TASK_ID", "team::route-1")
    assert [b["task_id"] for b in sa._open_discussion_blockers()] == ["route-probe"]

    monkeypatch.setenv("ARGUS_SKILL_TEAM_TASK_ID", "team::route-2")
    assert sa._open_discussion_blockers() == []
