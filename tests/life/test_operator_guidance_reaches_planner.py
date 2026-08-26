"""An operator instruction is not spent by whichever consumer reads it first.

Observed end to end on 2026-07-26 in a captured project event log. A mission blocked
asking for a CUDA-visible GPU. The operator answered:

    "There is no NVIDIA GPU on this machine and none can be provisioned. Do not
     wait for one. Redirect: implement the kernel logic and a correctness
     harness that runs on CPU..."

The Engineer received it and built exactly that. The Planner then proposed
"Make CUDA-visible NVIDIA GPU available and rerun environment audit" — a task
that can never succeed — because the pending-question resolver had *consumed*
the message from the inbox, so the Planner's own drain returned nothing and it
planned on against the assumption the operator had just refuted.

The inbox is a queue, so the answer does have to be consumed to avoid answering
twice. What must not happen is the guidance disappearing from every other reader.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor._idle_cycle import IdleCycleMixin
from argus_skill.manager.directive import render_active_steering


class _Supervisor(IdleCycleMixin):
    """Only the inbox seam is stubbed; the carryover logic is the real one."""

    def __init__(
        self,
        messages: list[str],
        root: Path,
        *,
        resolves: bool = True,
    ) -> None:
        self._messages = list(messages)
        self._resolves = resolves
        self.memory = LifeMemory.open(root)
        self.statuses: list[str] = []
        self.events: list[dict] = []
        self.config = SimpleNamespace(
            user_inbox=self._pop,
            pending_question_resolver=self._resolve,
        )

    def _pop(self):
        return self._messages.pop(0) if self._messages else None

    def _resolve(self, _item, _message):
        return {"resolved": self._resolves}

    # -- seams the mixin calls --
    def _emit(self, event: dict) -> None:
        self.events.append(event)

    def _emit_status(self, text: str) -> None:
        self.statuses.append(text)

    def _reset_idle_backoff(self) -> None:
        return None


_ANSWER = "There is no GPU and none can be provisioned. Do not wait for one."


def test_the_answer_that_unblocked_a_mission_reaches_the_next_plan(
    tmp_path: Path,
) -> None:
    supervisor = _Supervisor([_ANSWER], tmp_path)
    item = SimpleNamespace(id="item-1", pending_question="need a GPU")

    assert supervisor._resolve_pending_question_from_inbox([item]) is True
    # The inbox is now empty — this is the state the Planner used to see.
    assert supervisor._pop() is None

    assert supervisor._take_operator_guidance_carryover() == [_ANSWER]
    assert _ANSWER in render_active_steering(tmp_path)


def test_the_one_shot_carryover_is_handed_over_only_once(tmp_path: Path) -> None:
    """The immediate note is one-shot while the standing ledger persists."""
    supervisor = _Supervisor([_ANSWER], tmp_path)
    supervisor._resolve_pending_question_from_inbox(
        [SimpleNamespace(id="i", pending_question="q")]
    )

    assert supervisor._take_operator_guidance_carryover() == [_ANSWER]
    assert supervisor._take_operator_guidance_carryover() == []
    assert _ANSWER in render_active_steering(tmp_path)


def test_an_unresolved_question_carries_nothing_over(tmp_path: Path) -> None:
    """If the answer did not resolve anything, it was not consumed on our behalf."""
    supervisor = _Supervisor([_ANSWER], tmp_path, resolves=False)

    assert supervisor._resolve_pending_question_from_inbox(
        [SimpleNamespace(id="i", pending_question="q")]
    ) is False
    assert supervisor._take_operator_guidance_carryover() == []
    assert _ANSWER in render_active_steering(tmp_path)


def test_nothing_carries_over_when_the_inbox_was_empty(tmp_path: Path) -> None:
    supervisor = _Supervisor([], tmp_path)

    assert supervisor._resolve_pending_question_from_inbox(
        [SimpleNamespace(id="i", pending_question="q")]
    ) is False
    assert supervisor._take_operator_guidance_carryover() == []


def test_the_planning_intake_prepends_the_carryover() -> None:
    """The two halves must agree, or the carryover is written and never read."""
    import inspect

    from argus_skill.life.supervisor import _planning_cycle_intake

    source = inspect.getsource(_planning_cycle_intake.PlanningCycleIntakeMixin)
    assert "_take_operator_guidance_carryover()" in source
