"""Per-item codex SESSION ISOLATION (anti context-pollution).

The runner chains its codex thread across execute() calls. Left unchecked, a
brand-new, unrelated backlog item RESUMES the previous mission's codex session
and inherits all its context — a plain "你上一个任务干了什么" was resuming a
kernel-optimization session and reading its GROUND_TRUTH. The supervisor must
reset the runner's carried thread when the backlog ITEM changes, so a new item
starts fresh; only iteration cycles of the SAME item keep the thread.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)


@dataclass
class _Outcome:
    success: bool = True
    status: str = "done"
    stop_reason: str = ""
    rounds: int = 1
    chat_mode: bool = False
    final_message: str = "ok"
    last_thread_id: str | None = None


class _ThreadRunner:
    """Records the carried thread seen at each execute() and then chains a new
    one (as the real runner does), so the test can prove the supervisor resets
    it per item."""

    def __init__(self) -> None:
        self.backend = None
        self._next_seed_thread_id: str | None = None
        self.last_thread_id: str | None = None
        self.seen: list[str | None] = []

    def execute(self, *, objective: str, sink: Any, prelude_context: str = "",
                scope: str = "", original_objective: str = "") -> _Outcome:
        self.seen.append(self._next_seed_thread_id)
        sink.handle_event({"type": "round.main.completed",
                           "input_tokens": 10, "output_tokens": 5})
        # Simulate the real runner chaining its thread forward after a mission.
        self._next_seed_thread_id = f"tid-{objective[:6]}"
        self.last_thread_id = self._next_seed_thread_id
        return _Outcome()


def _sup(tmp_path: Path, runner: Any) -> LifeSupervisor:
    mem = LifeMemory.open(tmp_path / "life")
    cfg = LifeSupervisorConfig(budget=LifeBudget(), poll_interval_seconds=0.01)

    class _Sink:
        def handle_event(self, e: dict) -> None: ...
        def handle_stream_line(self, s: str, l: str) -> None: ...  # noqa: E741
        def close(self) -> None: ...

    return mem, LifeSupervisor(memory=mem, runner=runner, sink=_Sink(), config=cfg)


def test_new_item_starts_fresh_codex_session(tmp_path: Path) -> None:
    """Two DIFFERENT backlog items → each execute() sees a reset (None) carried
    thread, so the second never resumes the first's session."""
    runner = _ThreadRunner()
    mem, sup = _sup(tmp_path, runner)
    mem.backlog.add(BacklogItem.new(title="t1", objective="optimize the kernel",
                                    iterate=False))
    sup.tick()
    mem.backlog.add(BacklogItem.new(title="t2", objective="你上一个任务干了什么",
                                    iterate=False))
    sup.tick()
    assert runner.seen == [None, None], (
        "a new unrelated item must NOT inherit the prior mission's codex thread; "
        f"saw {runner.seen}"
    )
