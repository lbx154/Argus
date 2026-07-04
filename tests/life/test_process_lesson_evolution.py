"""Retired process-lesson write path.

Process lessons used to be reviewer-authored explanatory text that the harness
journaled and re-fed to Planner. The one-log/three-write policy retires that
path: reusable behavior must be explicit skill_ops or derived offline from
events.jsonl, not an extra role-written memory stream.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig


@dataclass
class _Outcome:
    process_lesson: str = ""


class _Runner:
    backend = None


def _sup(tmp_path: Path) -> tuple[LifeMemory, LifeSupervisor]:
    mem = LifeMemory.open(tmp_path / "life")
    cfg = LifeSupervisorConfig(budget=LifeBudget(), poll_interval_seconds=0.01)

    class _Sink:
        def handle_event(self, _e: dict) -> None: ...
        def handle_stream_line(self, _s: str, _l: str) -> None: ...  # noqa: E741
        def close(self) -> None: ...

    return mem, LifeSupervisor(memory=mem, runner=_Runner(), sink=_Sink(), config=cfg)


def test_process_lesson_hook_is_retired_no_write(tmp_path: Path) -> None:
    mem, sup = _sup(tmp_path)
    item = BacklogItem.new(title="t", objective="o")

    sup._maybe_journal_process_lesson(item, _Outcome(process_lesson="freeze the floor"))

    assert [
        e for e in mem.journal.all()
        if getattr(e, "kind", "") == "self_evolve.process_lesson"
    ] == []


def test_planner_render_does_not_surface_process_lessons(tmp_path: Path) -> None:
    _mem, sup = _sup(tmp_path)

    rendered = sup._render_journal_for_planner()

    assert "Recurring process lessons" not in rendered
