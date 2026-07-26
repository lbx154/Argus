"""An operator instruction is not spent by whichever consumer reads it first.

Observed end to end on 2026-07-26
(/tmp/argus-night/home/projects/f9a27b1c16a7/events.jsonl). A mission blocked
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

from types import SimpleNamespace

from argus_skill.life.supervisor._idle_cycle import IdleCycleMixin


class _Supervisor(IdleCycleMixin):
    """Only the inbox seam is stubbed; the carryover logic is the real one."""

    def __init__(self, messages: list[str], *, resolves: bool = True) -> None:
        self._messages = list(messages)
        self._resolves = resolves
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


def test_the_answer_that_unblocked_a_mission_reaches_the_next_plan() -> None:
    supervisor = _Supervisor([_ANSWER])
    item = SimpleNamespace(id="item-1", pending_question="need a GPU")

    assert supervisor._resolve_pending_question_from_inbox([item]) is True
    # The inbox is now empty — this is the state the Planner used to see.
    assert supervisor._pop() is None

    assert supervisor._take_operator_guidance_carryover() == [_ANSWER]


def test_the_carryover_is_handed_over_only_once() -> None:
    """It is guidance for the next plan, not a permanent banner."""
    supervisor = _Supervisor([_ANSWER])
    supervisor._resolve_pending_question_from_inbox(
        [SimpleNamespace(id="i", pending_question="q")]
    )

    assert supervisor._take_operator_guidance_carryover() == [_ANSWER]
    assert supervisor._take_operator_guidance_carryover() == []


def test_an_unresolved_question_carries_nothing_over() -> None:
    """If the answer did not resolve anything, it was not consumed on our behalf."""
    supervisor = _Supervisor([_ANSWER], resolves=False)

    assert supervisor._resolve_pending_question_from_inbox(
        [SimpleNamespace(id="i", pending_question="q")]
    ) is False
    assert supervisor._take_operator_guidance_carryover() == []


def test_nothing_carries_over_when_the_inbox_was_empty() -> None:
    supervisor = _Supervisor([])

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
