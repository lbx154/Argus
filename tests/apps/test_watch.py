"""Regression tests for ``argus-skill --watch`` state tracking."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from argus_skill.apps._inbox import format_inbox_event
from argus_skill.apps._watch import (
    _BudgetLineCache,
    _mission_context_lines,
    _read_backlog_rows,
    _select_current_backlog_row,
    _WatchState,
)
from argus_skill.core import project
from argus_skill.daemon.life_worker import read_continuous_state


def _write_events(path: Path, events: list[dict[str, object]], *, mode: str = "w") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = "\n".join(json.dumps(event, sort_keys=True) for event in events)
    if blob:
        blob += "\n"
    if mode == "a":
        with path.open("a", encoding="utf-8") as fh:
            fh.write(blob)
    else:
        path.write_text(blob, encoding="utf-8")


def _wait_until(predicate, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting for condition")


def _round_events(
    round_index: int,
    *,
    kind: str = "round.started",
    input_tokens: int = 3,
    output_tokens: int = 2,
    review_input_tokens: int = 5,
    review_output_tokens: int = 7,
) -> list[dict[str, object]]:
    return [
        {"type": kind, "round_index": round_index},
        {
            "type": "round.main.completed",
            "round_index": round_index,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        {
            "type": "round.review.completed",
            "round_index": round_index,
            "input_tokens": review_input_tokens,
            "output_tokens": review_output_tokens,
        },
    ]


def test_watch_state_tracks_memory_backend_round_events(tmp_path: Path) -> None:
    current = tmp_path / "events.jsonl"
    state = _WatchState(events_path=current, roll_path=tmp_path / "events.jsonl.1")

    _write_events(
        current,
        [
            {"type": "life.mission.started", "item_id": "mission-abc-123456"},
            *_round_events(1, kind="round.started", input_tokens=800, output_tokens=200,
                           review_input_tokens=100, review_output_tokens=50),
            {"type": "life.mission.completed", "success": True},
        ],
    )

    state.drain()

    assert state.mission.status == "done"
    assert state.mission.rounds == 1
    assert state.mission.tokens_in == 900
    assert state.mission.tokens_out == 250


def test_watch_state_accumulates_more_than_twenty_events(tmp_path: Path) -> None:
    current = tmp_path / "events.jsonl"
    state = _WatchState(events_path=current, roll_path=tmp_path / "events.jsonl.1")

    events: list[dict[str, object]] = [{"type": "life.mission.started", "item_id": "mission-xyz"}]
    for round_index in range(1, 8):
        events.extend(_round_events(round_index, kind="round.start"))
    events.append({"type": "life.mission.completed", "success": True})
    _write_events(current, events)

    state.drain()

    assert state.mission.status == "done"
    assert state.mission.rounds == 7
    assert state.mission.tokens_in == 56
    assert state.mission.tokens_out == 63


def test_watch_state_recovers_from_rollover(tmp_path: Path) -> None:
    current = tmp_path / "events.jsonl"
    roll = tmp_path / "events.jsonl.1"
    state = _WatchState(events_path=current, roll_path=roll)

    _write_events(
        current,
        [
            {"type": "life.mission.started", "item_id": "mission-roll"},
            *_round_events(1, kind="round.start"),
        ],
    )
    state.drain()

    _write_events(current, _round_events(2, kind="round.start"), mode="a")
    current.replace(roll)
    _write_events(
        current,
        [
            *_round_events(3, kind="round.started"),
            {"type": "life.mission.completed", "success": True},
        ],
    )

    state.drain()

    assert state.mission.status == "done"
    assert state.mission.rounds == 3
    assert state.mission.tokens_in == 24
    assert state.mission.tokens_out == 27


def test_watch_state_recovers_from_truncation(tmp_path: Path) -> None:
    current = tmp_path / "events.jsonl"
    state = _WatchState(events_path=current, roll_path=tmp_path / "events.jsonl.1")

    _write_events(
        current,
        [
            {"type": "life.mission.started", "item_id": "mission-old"},
            *_round_events(1, kind="round.started", input_tokens=10, output_tokens=11,
                           review_input_tokens=12, review_output_tokens=13),
        ],
    )
    state.drain()

    _write_events(
        current,
        [
            {"type": "life.mission.started", "item_id": "mission-new"},
            *_round_events(1, kind="round.started", input_tokens=20, output_tokens=21,
                           review_input_tokens=22, review_output_tokens=23),
            {"type": "life.mission.completed", "success": True},
        ],
    )

    state.drain()

    assert state.mission.status == "done"
    assert state.mission.item_id == "mission-new"
    assert state.mission.rounds == 1
    assert state.mission.tokens_in == 42
    assert state.mission.tokens_out == 44


def test_inbox_event_formatter_distinguishes_queue_and_drain() -> None:
    queued = format_inbox_event({
        "type": "life.inbox.queued",
        "source": "cli.notify",
        "text": "please check the status output",
    })
    drained = format_inbox_event({
        "type": "life.inbox.drained",
        "count": 1,
        "messages": ["please check the status output"],
    })

    assert queued == "📥 life.inbox.queued · cli.notify · please check the status output"
    assert drained == "📤 life.inbox.drained · 1 message · please check the status output"


def test_watch_mission_context_lines_include_running_task_and_continuous_state(tmp_path: Path) -> None:
    backlog = tmp_path / "backlog.jsonl"
    backlog.write_text(
        "\n".join(
            [
                json.dumps({
                    "id": "older",
                    "status": "running",
                    "title": "older task",
                    "objective": "first stale row",
                    "started_ts": 10.0,
                }),
                json.dumps({
                    "id": "newer",
                    "status": "running",
                    "title": "newer task",
                    "objective": "current task row",
                    "started_ts": 20.0,
                }),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "continuous.json").write_text(
        json.dumps(
            {
                "enabled": False,
                "objective": "keep going",
                "done_reason": "planner declared project done",
                "done_at": "2026-05-12T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    rows = _read_backlog_rows(backlog)
    current = _select_current_backlog_row(rows)
    mission = _WatchState(events_path=tmp_path / "events.jsonl", roll_path=tmp_path / "events.jsonl.1").mission
    mission.status = "running"
    mission.rounds = 3
    mission.tokens_in = 10
    mission.tokens_out = 20

    rendered = "\n".join(
        f"{label}: {value}"
        for label, value in _mission_context_lines(
            mission=mission,
            current_row=current,
            continuous=read_continuous_state(tmp_path),
        )
    )

    assert "title: newer task" in rendered
    assert "objective: current task row" in rendered
    assert "continuous: done" in rendered
    assert "continuous objective: keep going" in rendered
    assert "done_reason: planner declared project done" in rendered
    assert "done_at: 2026-05-12T00:00:00Z" in rendered


def test_budget_line_cache_reuses_previous_result_until_inputs_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus_skill.apps._watch as watch_mod

    cache = _BudgetLineCache()
    journal_path = tmp_path / "journal.jsonl"
    journal_path.write_text("", encoding="utf-8")
    calls = {"n": 0}

    class _FakeBudget:
        per_mission_cap_usd = 2.5
        daily_cap_usd = 5.0

        def remaining_today(self, journal: object) -> float:  # noqa: ARG002
            calls["n"] += 1
            return 3.0

    monkeypatch.setattr(watch_mod, "resolve_effective_budget", lambda status: _FakeBudget())
    status = Namespace(
        alive=True,
        per_mission_cap_usd=2.5,
        daily_cap_usd=5.0,
    )

    first = cache.render(journal_path=journal_path, journal=object(), status=status)
    second = cache.render(journal_path=journal_path, journal=object(), status=status)
    journal_path.write_text('{"ts": 1, "cost_usd": 1.0}\n', encoding="utf-8")
    third = cache.render(journal_path=journal_path, journal=object(), status=status)

    assert first == "budget   : per-mission $2.50 · daily $5.00 · remaining $3.00"
    assert second == first
    assert third == first
    assert calls["n"] == 2


def test_watch_subprocess_renders_inbox_guidance_and_keeps_offset(tmp_path: Path) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    project_root.mkdir(parents=True, exist_ok=True)

    (project_root / "backlog.jsonl").write_text(
        json.dumps(
            {
                "id": "running-1",
                "status": "running",
                "title": "ship the cockpit",
                "objective": "show running task and continuous state",
                "started_ts": 20.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (project_root / "continuous.json").write_text(
        json.dumps(
            {
                "enabled": False,
                "objective": "keep the operator informed",
                "done_reason": "planner declared project done",
                "done_at": "2026-05-12T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    inbox = project_root / "inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text(json.dumps({"text": "please inspect"}) + "\n", encoding="utf-8")
    offset_path = project_root / "inbox.offset"
    offset_path.write_text("0", encoding="utf-8")
    _write_events(
        global_root / "journal.jsonl",
        [
            {
                "id": "journal-1",
                "ts": time.time(),
                "kind": "mission_complete",
                "title": "spent budget",
                "summary": "daily spend",
                "tags": [],
                "cost_usd": 1.5,
                "extra": {},
            }
        ],
    )

    _write_events(
        project_root / "events.jsonl",
        [
            {
                "type": "life.inbox.queued",
                "source": "cli.notify",
                "text": "please inspect",
            },
            {
                "type": "life.inbox.drained",
                "count": 1,
                "messages": ["please inspect"],
            },
        ],
    )
    before = offset_path.read_text(encoding="utf-8")

    env = os.environ.copy()
    env.update({
        "PYTHONUNBUFFERED": "1",
        "ARGUS_SKILL_PER_MISSION_CAP_USD": "2.5",
        "ARGUS_SKILL_DAILY_CAP_USD": "5.0",
        "COLUMNS": "260",
        "LINES": "60",
    })
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "argus_skill",
            "--watch",
            "--life-dir",
            str(global_root),
        ],
        cwd=repo_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(1.0)
        proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=10)

    output = stdout + stderr
    after = offset_path.read_text(encoding="utf-8")
    assert proc.returncode == 0, proc
    assert "budget   : per-mission $2.50 · daily $5.00 · remaining $3.50" in output
    assert "title" in output
    assert "ship the cockpit" in output
    assert "objective" in output
    assert "show running task and continuous state" in output
    assert "continuous" in output
    assert "keep the operator informed" in output
    assert "planner declared project done" in output
    assert "2026-05-12T00:00:00Z" in output
    assert "inbox=1 pending" in output
    assert "life.inbox.queued" in output
    assert "life.inbox.drained" in output
    assert before == after


def test_watch_subprocess_redirected_output_flushes_and_exits_on_sigterm(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    project_root.mkdir(parents=True, exist_ok=True)

    pid = os.getpid()
    started_at = datetime.now(timezone.utc).isoformat()
    (project_root / "daemon.pid").write_text(str(pid), encoding="ascii")
    (project_root / "daemon.status.json").write_text(
        json.dumps(
            {
                "pid": pid,
                "started_at_iso": started_at,
                "backend": "memory",
                "life_dir": str(global_root),
                "per_mission_cap_usd": 2.5,
                "daily_cap_usd": 5.0,
            }
        ),
        encoding="utf-8",
    )
    (project_root / "backlog.jsonl").write_text(
        json.dumps(
            {
                "id": "running-1",
                "status": "running",
                "title": "ship the cockpit",
                "objective": "show running task and flushed output",
                "started_ts": 20.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (project_root / "continuous.json").write_text(
        json.dumps(
            {
                "enabled": False,
                "objective": "keep the operator informed",
                "done_reason": "planner declared project done",
                "done_at": "2026-05-12T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    _write_events(
        global_root / "journal.jsonl",
        [
            {
                "id": "journal-1",
                "ts": time.time(),
                "kind": "mission_complete",
                "title": "spent budget",
                "summary": "daily spend",
                "tags": [],
                "cost_usd": 1.0,
                "extra": {},
            }
        ],
    )
    _write_events(
        project_root / "events.jsonl",
        [
            {"type": "life.mission.started", "item_id": "mission-watch"},
            {"type": "round.start", "round_index": 1},
            {
                "type": "round.main.completed",
                "round_index": 1,
                "input_tokens": 10,
                "output_tokens": 20,
            },
            {
                "type": "life.inbox.queued",
                "source": "cli.notify",
                "text": "watch is alive",
            },
        ],
    )

    output_path = tmp_path / "watch.out"
    env = os.environ.copy()
    env.update({
        "PYTHONUNBUFFERED": "1",
        "ARGUS_SKILL_PER_MISSION_CAP_USD": "2.5",
        "ARGUS_SKILL_DAILY_CAP_USD": "5.0",
        "COLUMNS": "260",
        "LINES": "60",
    })
    with output_path.open("w", encoding="utf-8") as out:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "argus_skill",
                "--watch",
                "--life-dir",
                str(global_root),
            ],
            cwd=repo_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            text=True,
        )
        try:
            _wait_until(lambda: output_path.stat().st_size > 0, timeout=10.0)
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)

    output = output_path.read_text(encoding="utf-8")
    assert proc.returncode == 0, proc
    assert "argus-skill watch" in output
    assert "budget   :" in output
    assert "daemon" in output
    assert "alive" in output
    assert "running-1" in output or "ship the cockpit" in output
    assert "life.mission.started" in output or "round.main.completed" in output
    assert "watch is alive" in output


def test_watch_subprocess_shows_paused_budget_when_exhausted(tmp_path: Path) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    (global_root / "projects" / fingerprint).mkdir(parents=True, exist_ok=True)

    _write_events(
        global_root / "journal.jsonl",
        [
            {
                "id": "journal-1",
                "ts": time.time(),
                "kind": "mission_complete",
                "title": "spent budget",
                "summary": "daily spend",
                "tags": [],
                "cost_usd": 5.0,
                "extra": {},
            }
        ],
    )

    env = os.environ.copy()
    env.update({
        "PYTHONUNBUFFERED": "1",
        "ARGUS_SKILL_PER_MISSION_CAP_USD": "2.5",
        "ARGUS_SKILL_DAILY_CAP_USD": "5.0",
        "COLUMNS": "260",
        "LINES": "60",
    })
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "argus_skill",
            "--watch",
            "--life-dir",
            str(global_root),
        ],
        cwd=repo_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(1.0)
        proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=10)

    output = stdout + stderr
    assert proc.returncode == 0, proc
    assert "budget   : per-mission $2.50 · daily $5.00 · remaining $0.00 (paused)" in output
