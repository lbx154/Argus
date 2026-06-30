"""Self-evolution closure for PROCESS data ("过程数据").

The reviewer judges a reusable PROCESS lesson every mission (how the agent
worked, where it wasted rounds, a workaround that helped). It used to be
WRITE-ONLY — produced and never consumed. Now the supervisor journals it as
``self_evolve.process_lesson`` and the memory prelude surfaces recent lessons
into future missions, so the agent learns from its own process.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from argus_skill.life import MemoryBundle
from argus_skill.life.memory import BacklogItem, JournalEntry, LifeMemory
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)


def test_recent_process_lessons_dedup_newest_first() -> None:
    m = MemoryBundle.for_cwd(tempfile.mkdtemp())
    m.init()
    for lesson in ["measure before claiming", "measure before claiming", "freeze the floor"]:
        m.project.memory.append(JournalEntry.new(
            kind="self_evolve.process_lesson", title="process lesson",
            summary=lesson, tags=["self_evolve"], extra={"lesson": lesson}))
    got = m.project.recent_process_lessons(limit=3)
    assert got == ["freeze the floor", "measure before claiming"]  # newest first, deduped


def test_process_lessons_journaled_not_force_injected() -> None:
    m = MemoryBundle.for_cwd(tempfile.mkdtemp())
    m.init()
    m.project.memory.append(JournalEntry.new(
        kind="self_evolve.process_lesson", title="process lesson",
        summary="x", tags=["self_evolve"],
        extra={"lesson": "Run the official eval before claiming a speedup."}))
    # Process lessons are NOT force-injected into the prompt prelude (that would
    # be prompt bloat / re-pollution). They are journaled as durable, retrievable
    # data — to be distilled into skills the matcher pulls when relevant.
    pre = m.render_prelude(objective="optimize a kernel")
    assert "Run the official eval before claiming a speedup." not in pre
    assert m.project.recent_process_lessons() == [
        "Run the official eval before claiming a speedup."]


@dataclass
class _Outcome:
    success: bool = True
    status: str = "done"
    stop_reason: str = ""
    rounds: int = 1
    process_lesson: str = ""


class _Runner:
    backend = None


def _sup(tmp_path: Path) -> tuple[LifeMemory, LifeSupervisor]:
    mem = LifeMemory.open(tmp_path / "life")
    cfg = LifeSupervisorConfig(budget=LifeBudget(), poll_interval_seconds=0.01)

    class _Sink:
        def handle_event(self, e: dict) -> None: ...
        def handle_stream_line(self, s: str, l: str) -> None: ...  # noqa: E741
        def close(self) -> None: ...

    return mem, LifeSupervisor(memory=mem, runner=_Runner(), sink=_Sink(), config=cfg)


def test_supervisor_journals_process_lesson_deduped(tmp_path: Path) -> None:
    mem, sup = _sup(tmp_path)
    item = BacklogItem.new(title="t", objective="o")
    sup._maybe_journal_process_lesson(item, _Outcome(process_lesson="freeze the floor"))
    sup._maybe_journal_process_lesson(item, _Outcome(process_lesson="freeze the floor"))
    sup._maybe_journal_process_lesson(item, _Outcome(process_lesson=""))  # empty → no-op
    entries = [e for e in mem.journal.all()
               if getattr(e, "kind", "") == "self_evolve.process_lesson"]
    assert len(entries) == 1  # deduped, empty skipped
    assert entries[0].extra["lesson"] == "freeze the floor"
