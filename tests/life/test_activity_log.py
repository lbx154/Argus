from __future__ import annotations

import re
from pathlib import Path

from argus_skill.life.activity_log import (
    ACTIVITY_FILE,
    ActivityLogSink,
    render_line,
)


class _Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.streams: list[tuple[str, str]] = []
        self.closed = False

    def handle_event(self, event: dict) -> None:
        self.events.append(event)

    def handle_stream_line(self, stream: str, line: str) -> None:
        self.streams.append((stream, line))

    def close(self) -> None:
        self.closed = True


def test_render_line_milestone_and_drop() -> None:
    assert render_line(
        {"type": "life.mission.started", "missions_started": 3, "title": "Run benchmark"}
    ) == "MISSION  start   #3  Run benchmark"

    completed = render_line(
        {
            "type": "life.mission.completed",
            "status": "done",
            "success": True,
            "rounds": 4,
            "cost_usd": 1.2345,
        }
    )
    assert completed is not None
    assert "MISSION  done    done" in completed
    assert "rounds=4" in completed
    assert "cost=$1.23" in completed

    verdict = render_line(
        {
            "type": "life.planner.verdict",
            "project_done": False,
            "task_count": 2,
            "enqueued_tasks": 1,
            "skipped_duplicate_tasks": 1,
            "reason": "needs full-scale evidence",
        }
    )
    assert verdict is not None
    assert "PLANNER  verdict" in verdict
    assert "enqueued=1" in verdict
    assert "skipped_dup=1" in verdict
    assert "needs full-scale evidence" in verdict


def test_render_line_round_milestones() -> None:
    # Reviewer verdict per round is the key debugging signal.
    verdict = render_line({
        "type": "round.review.completed",
        "round_index": 3,
        "status": "continue",
        "confidence": 0.4,
        "reason": "benchmark provenance missing",
    })
    assert verdict is not None
    assert "ROUND" in verdict and "reviewer" in verdict
    assert "round=3" in verdict and "verdict=continue" in verdict
    assert "benchmark provenance missing" in verdict

    built = render_line({"type": "round.main.completed", "round_index": 3})
    assert built == "ROUND    engineer  round=3  built"

    warn = render_line({
        "type": "engineer.failure_nudge",
        "round": 4,
        "text": "repeated tool failures detected — advisory injected",
    })
    assert warn is not None
    assert warn.startswith("WARN") and "round=4" in warn


def test_render_line_drops_noise() -> None:
    # Not on the allow-list -> dropped.
    assert render_line({"type": "skill.outcome", "status": "done"}) is None
    assert render_line({"type": "loop.done", "text": "status=done"}) is None
    assert render_line({"type": "life.telemetry", "cost_usd": 5.0}) is None
    # Idle status chatter -> dropped.
    assert render_line({"type": "life.status", "text": "backlog empty; exiting"}) is None
    # Actionable status -> kept.
    kept = render_line({"type": "life.status", "text": "planner: project done"})
    assert kept == "STATUS   planner: project done"


def test_render_line_manager_stage_decision() -> None:
    # A real transition renders a MANAGER milestone with the stage move.
    line = render_line({
        "type": "life.manager.stage_decision",
        "action": "advance",
        "current_stage": "benchmark",
        "target_stage": "run",
        "reason": "benchmark checklist satisfied",
    })
    assert line is not None
    assert "MANAGER" in line and "advance" in line and "benchmark → run" in line
    # A HOLD is not a milestone (stage unchanged) -> dropped.
    assert render_line({
        "type": "life.manager.stage_decision",
        "action": "hold",
        "current_stage": "benchmark",
        "target_stage": "benchmark",
    }) is None


def test_sink_passes_through_and_writes_only_milestones(tmp_path: Path) -> None:
    rec = _Recorder()
    sink = ActivityLogSink(rec, life_dir=tmp_path)

    sink.handle_event({"type": "life.mission.started", "missions_started": 1, "title": "T"})
    sink.handle_event({"type": "skill.outcome", "status": "done"})  # noise
    sink.handle_event({"type": "life.mission.completed", "status": "done", "success": True})
    sink.handle_stream_line("stdout", "noisy raw line")
    sink.close()

    # Every event (including noise) reaches downstream untouched.
    assert len(rec.events) == 3
    assert rec.streams == [("stdout", "noisy raw line")]
    assert rec.closed is True

    log_text = (tmp_path / ACTIVITY_FILE).read_text(encoding="utf-8")
    lines = [ln for ln in log_text.splitlines() if ln.strip()]
    # Only the two milestones are written; the skill.outcome noise is not.
    assert len(lines) == 2
    assert "MISSION  start" in lines[0]
    assert "MISSION  done" in lines[1]
    assert "skill.outcome" not in log_text


def test_sink_survives_downstream_failure(tmp_path: Path) -> None:
    class _Boom:
        def handle_event(self, event: dict) -> None:
            raise RuntimeError("downstream broken")

    sink = ActivityLogSink(_Boom(), life_dir=tmp_path)
    # Must not raise even though downstream throws.
    sink.handle_event({"type": "life.mission.started", "missions_started": 1, "title": "T"})
    log_text = (tmp_path / ACTIVITY_FILE).read_text(encoding="utf-8")
    assert "MISSION  start" in log_text


def test_sink_groups_indents_and_timestamps(tmp_path: Path) -> None:
    sink = ActivityLogSink(None, life_dir=tmp_path)
    base = 1_000_000.0
    seq = [
        {"type": "life.mission.started", "item_id": "m1", "missions_started": 1, "title": "T"},
        {"type": "round.main.completed", "round_index": 1},
        {"type": "life.mission.completed", "item_id": "m1", "success": True, "rounds": 1},
    ]
    for i, ev in enumerate(seq):
        ev["ts"] = base + i * 30
        sink.handle_event(ev)

    text = (tmp_path / ACTIVITY_FILE).read_text(encoding="utf-8")
    lines = text.splitlines()
    # Tree glyphs group the mission: opens, interior round, closes.
    assert "┌─" in lines[0] and "MISSION  start" in lines[0]
    assert "│" in lines[1] and "round=1" in lines[1]
    assert "└─" in lines[2] and "MISSION  done" in lines[2]
    # Local HH:MM:SS timestamp + relative gap on the head lines.
    assert re.match(r"^\d\d:\d\d:\d\d \(\+0s\)", lines[0])
    assert "(+30s)" in lines[1]


def test_sink_wraps_long_reason_without_truncation(tmp_path: Path) -> None:
    sink = ActivityLogSink(None, life_dir=tmp_path)
    reason = (
        "benchmark provenance missing because the harness never recorded the "
        "dataset checksum and we cannot reproduce the score on a clean machine "
        "so the round must continue until the evidence chain is complete again"
    )
    sink.handle_event({"type": "life.mission.started", "item_id": "m1", "missions_started": 1, "title": "T"})
    sink.handle_event({
        "type": "round.review.completed",
        "round_index": 1,
        "status": "continue",
        "confidence": 0.4,
        "reason": reason,
    })
    text = (tmp_path / ACTIVITY_FILE).read_text(encoding="utf-8")
    assert "…" not in text  # never truncated
    assert "↳" in text      # reason wrapped onto a continuation block
    # Every word of the long reason survives somewhere in the file.
    flat = " ".join(text.split())
    for word in reason.split():
        assert word in flat

