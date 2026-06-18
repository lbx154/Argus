"""Signal C wiring — the process_lesson -> distill -> metacritic loop.

The reviewer's per-mission ``process_lesson``s were write-only (events.jsonl) until
this hook closed the loop: on a cadence, the supervisor distills the quantified
process ledger into SHADOW process lessons (diagnosis only; the apply path stays
operator-gated, outside the package).
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def handle_event(self, event: dict) -> None:
        self.events.append(event)


class _Runner:
    pass


_LESSON_JSON = json.dumps([{
    "dominant_pattern": "wasted re-scoring rounds",
    "incentive_contradiction": "the prompt says trust the recorded score but a gate re-verifies it",
    "evidence": "1 process_lesson over 1 mission",
    "proposed_process_fix": "skip re-verification of an already-scored candidate",
}])


def _events_with_process_lesson(life: Path) -> None:
    (life / "events.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"type": "life.mission.started", "item_id": "m1"},
        {"type": "round.review.completed", "round_index": "1", "status": "done",
         "process_lesson": "Re-scoring an already-scored candidate wastes a round."},
        {"type": "life.mission.completed", "item_id": "m1", "success": "True",
         "status": "done", "rounds": "1", "cost_usd": "0.5"},
    ]) + "\n", encoding="utf-8")


def _make_sup(tmp_path, *, sink, planner_runner=None, cadence=1):
    life = tmp_path / "life"
    mem = LifeMemory.open(life)
    mem.init()
    sup = LifeSupervisor(
        memory=mem, runner=_Runner(), sink=sink, planner_runner=planner_runner,
        config=LifeSupervisorConfig(
            budget=LifeBudget(), poll_interval_seconds=0.01, telemetry_dir=life,
            process_metacritic_cadence=cadence),
    )
    return sup, life


def test_process_distiller_runs_metacritic_on_cadence(tmp_path):
    sink = _Sink()
    planner = MemoryBackend()
    planner.queue("process_metacritic", CannedResponse(message=_LESSON_JSON))
    sup, life = _make_sup(tmp_path, sink=sink, planner_runner=planner, cadence=1)
    _events_with_process_lesson(life)

    sup._maybe_run_process_distiller(None, None)

    out = list((life / "process_lessons").glob("process_lessons_*.json"))
    assert len(out) == 1, "shadow lessons must be persisted"
    saved = json.loads(out[0].read_text(encoding="utf-8"))
    assert saved[0]["dominant_pattern"] == "wasted re-scoring rounds"
    assert any(e.get("type") == "process_metacritic.lessons" for e in sink.events)
    assert any(label == "process_metacritic" for label, _, _ in planner.history)


def test_no_run_before_cadence(tmp_path):
    sink = _Sink()
    planner = MemoryBackend()
    planner.queue("process_metacritic", CannedResponse(message=_LESSON_JSON))
    sup, life = _make_sup(tmp_path, sink=sink, planner_runner=planner, cadence=5)
    _events_with_process_lesson(life)

    sup._maybe_run_process_distiller(None, None)  # only 1 of 5 missions

    assert not (life / "process_lessons").exists()
    assert not any(label == "process_metacritic" for label, _, _ in planner.history)


def test_no_run_without_process_lessons(tmp_path):
    sink = _Sink()
    planner = MemoryBackend()
    planner.queue("process_metacritic", CannedResponse(message=_LESSON_JSON))
    sup, life = _make_sup(tmp_path, sink=sink, planner_runner=planner, cadence=1)
    (life / "events.jsonl").write_text(json.dumps(
        {"type": "life.mission.completed", "item_id": "m1", "success": "True",
         "status": "done", "rounds": "1", "cost_usd": "0"}) + "\n", encoding="utf-8")

    sup._maybe_run_process_distiller(None, None)

    assert not (life / "process_lessons").exists()
    assert not any(label == "process_metacritic" for label, _, _ in planner.history)


def test_no_backend_disables_signal_c(tmp_path):
    sink = _Sink()
    sup, life = _make_sup(tmp_path, sink=sink, planner_runner=None, cadence=1)
    _events_with_process_lesson(life)

    sup._maybe_run_process_distiller(None, None)

    assert not (life / "process_lessons").exists()
