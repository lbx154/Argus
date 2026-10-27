"""Replay of s-efe9deac, 10-27 07:03:32: the primary waited out a helper's mission.

The Planner the primary worker started at 07:03:28 was superseded at
07:03:32, ending its pass with ``planner_retry`` and no backoff. Helper
parallel-1 was still running CARE (96a7), claimed at 05:12; the daemon logged
the pass only at 07:32:55, seven seconds after CARE finished, and the Planner
started again at 07:33:21. The fixture keeps those events, the two backlog
rows and the daemon log lines.

The second replay is the operator's stop-after-current at 14:15:56 that day,
while parallel-1 ran a mission and the primary sat in its backoff.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from argus.daemon.life_worker import LifeWorker, LifeWorkerConfig

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac"
FIXTURE = json.loads((FIXTURES / "drain_0703.json").read_text(encoding="utf-8"))
AFTER_CURRENT = json.loads((FIXTURES / "drain_after_current_1415.json").read_text(encoding="utf-8"))
POLL = 0.5


def _logged(fixture: dict, line: str) -> float:
    return datetime.strptime(
        line[:23] + fixture["daemon_log_utc_offset"], "%Y-%m-%d %H:%M:%S,%f%z"
    ).timestamp()


def _pass_summary(line: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in line.split("drain pass ", 1)[1].split())


def _rf_state(worker: LifeWorker, root: Path, primary: object, helper: object) -> SimpleNamespace:
    """What the main loop reads, for a primary and one helper."""
    return SimpleNamespace(
        runtime_root=root,
        cfg=worker.config,
        runner=SimpleNamespace(manager=None),
        sink=SimpleNamespace(handle_event=lambda _event: None),
        sup=primary,
        supervisors=[primary, helper],
    )


def _recorded() -> tuple[float, float, dict, dict[str, str], float]:
    events = FIXTURE["events"]
    superseded = next(e["ts"] for e in events if e["type"] == "life.planner.superseded")
    replanned = next(e["ts"] for e in events if e["type"] == "life.planner.start" and e["ts"] > superseded)
    care = next(row for row in FIXTURE["backlog"] if row["running_owner"] != "primary")
    line = FIXTURE["daemon_log"][-1]
    return superseded, replanned, care, _pass_summary(line), _logged(FIXTURE, line)


def test_primary_replans_within_a_poll_while_a_helper_runs_a_long_mission(tmp_path: Path) -> None:
    superseded, replanned, care, summary, logged = _recorded()
    # As recorded: the pass ended at once, but was logged only after CARE.
    assert summary == {"stopped_by": "planner_retry", "suggested_sleep": "0"}
    assert care["started_ts"] < superseded < care["finished_ts"] < logged < replanned
    assert logged - superseded > 1700

    worker = LifeWorker(LifeWorkerConfig(
        life_dir=tmp_path, backend="memory", poll_interval=POLL, continuous_open_ended=True,
    ))
    care_running = threading.Event()
    care_done = threading.Event()
    retried: list[float] = []
    replans: list[float] = []

    def primary_run() -> dict:
        if not retried:
            # The superseded Planner, while CARE runs on the helper.
            assert care_running.wait(5.0)
            retried.append(time.monotonic())
            return {"stopped_by": summary["stopped_by"], "suggested_sleep": float(summary["suggested_sleep"])}
        replans.append(time.monotonic())
        worker._stop.set()
        return {"stopped_by": "stop_requested"}

    def helper_run() -> dict:
        if not care_running.is_set():
            care_running.set()
            care_done.wait(30.0)
        return {"stopped_by": "backlog_empty"}

    primary = SimpleNamespace(config=SimpleNamespace(worker_id="primary"), run=primary_run)
    helper = SimpleNamespace(config=SimpleNamespace(worker_id=care["running_owner"]), run=helper_run)
    loop = threading.Thread(
        target=worker._rf_main_loop, args=(_rf_state(worker, tmp_path, primary, helper),), daemon=True,
    )
    loop.start()
    try:
        deadline = time.monotonic() + 10 * POLL
        while not replans and time.monotonic() < deadline:
            time.sleep(0.01)
        assert replans, "the primary waited for the helper's mission instead of replanning"
        assert not care_done.is_set()
        # One poll of sleep after ``planner_retry``, its own backoff, not the
        # 29 minutes CARE had left.
        assert POLL <= replans[0] - retried[0] < 2 * POLL
    finally:
        care_done.set()
        worker._stop.set()
        loop.join(10.0)
    assert not loop.is_alive()


def test_stop_after_current_lets_a_helper_finish_its_mission_and_claim_nothing_more(tmp_path: Path) -> None:
    """Helper parallel-1 claimed 02a2583442f7 at 14:10:18; the primary's pass
    ended ``claim_lost`` with a 300 s backoff. The operator's stop-after-current
    landed at 14:15:57, the mission ran on to 14:25:44 (its review handed the
    plan to the Planner, whose turn ended at 14:28:53) and the process stopped
    then. Helpers now outlive primary passes, so the drain must still end each
    one after the pass it is in: the mission is not interrupted, the process
    waits for it, and no pass starts after the request.
    """
    events = AFTER_CURRENT["events"]
    started = next(e["ts"] for e in events if e["type"] == "life.mission.started")
    drained = next(
        e["ts"] for e in events if e["type"] == "daemon.command.completed" and e["operation"] == "drain"
    )
    completed = next(e["ts"] for e in events if e["type"] == "life.mission.completed")
    *_, last_pass, stopped = AFTER_CURRENT["daemon_log"]
    summary = _pass_summary(last_pass)
    # As recorded: the drain landed mid-mission, the process stopped after it.
    assert summary == {"stopped_by": "claim_lost", "suggested_sleep": "300.0"}
    assert "stopping cleanly" in stopped
    assert started < drained < completed < _logged(AFTER_CURRENT, stopped)

    worker = LifeWorker(LifeWorkerConfig(
        life_dir=tmp_path, backend="memory", poll_interval=POLL, continuous_open_ended=True,
    ))
    mission_running = threading.Event()
    mission_done = threading.Event()
    drain_requested = threading.Event()
    primary_passes: list[bool] = []  # per pass: started after the request
    helper_passes: list[bool] = []

    def primary_run() -> dict:
        primary_passes.append(drain_requested.is_set())
        if len(primary_passes) == 1:
            # The helper claimed the mission first.
            assert mission_running.wait(5.0)
        return {"stopped_by": summary["stopped_by"], "suggested_sleep": float(summary["suggested_sleep"])}

    def helper_run() -> dict:
        helper_passes.append(drain_requested.is_set())
        if len(helper_passes) == 1:
            mission_running.set()
            assert mission_done.wait(30.0)
            return {"stopped_by": "stop_event signalled"}
        return {"stopped_by": "backlog_empty"}

    primary = SimpleNamespace(config=SimpleNamespace(worker_id="primary"), run=primary_run)
    helper = SimpleNamespace(config=SimpleNamespace(worker_id=AFTER_CURRENT["mission_owner"]), run=helper_run)
    loop = threading.Thread(
        target=worker._rf_main_loop, args=(_rf_state(worker, tmp_path, primary, helper),), daemon=True,
    )
    loop.start()
    try:
        assert mission_running.wait(5.0)
        deadline = time.monotonic() + 5.0
        while not primary_passes and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(POLL)  # the primary is in its backoff, as at 14:15:57
        worker.request_process_stop(drain=True)
        drain_requested.set()
        loop.join(3 * POLL)
        assert loop.is_alive(), "the process left before the current mission finished"
        assert not worker._mission_stop.is_set()
        mission_done.set()
        loop.join(4 * POLL)
        assert not loop.is_alive(), "the process outlived the current mission"
    finally:
        mission_done.set()
        worker._stop.set()
        loop.join(10.0)
    assert not loop.is_alive()
    assert helper_passes == [False]
    assert primary_passes and not any(primary_passes)
