"""A wait with no contract is where the Manager review matters most.

Measured on 2026-07-26 in a captured project event log: a kernel_engineering
campaign emitted several consecutive `life.planner.waiting` verdicts, every one with
`waiting_contract: null`, and produced zero Manager stage reviews. Thirteen
provider calls, no progress, and the Planner had already said exactly what was
wrong:

    "Scope checklist is satisfied, but only the Manager may advance
     current_stage from scope to environment."

The one authority that could act was never asked, because
`_reconcile_open_ended_planner_waiting` returned immediately when the blocker
fingerprint and recheck token were missing — making the "every new non-operator
wait gets one immediate liveness review" that its own docstring promises
unreachable for any wait the Planner did not fully annotate.

Asking the Manager more often is plumbing, not a science judgment: the Manager
still decides HOLD versus ROLLBACK, and nothing here reads the research.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class _Verdict(SimpleNamespace):
    pass


def _verdict(**kw):
    base = {
        "waiting": True,
        "project_done": False,
        "new_tasks": [],
        "waiting_contract": None,
        "waiting_reason": "only the Manager may advance current_stage",
        "reason": "",
    }
    base.update(kw)
    return _Verdict(**base)


class _Probe:
    """The real reconciliation, with only its Manager call and stage stubbed."""

    def __init__(self, *, stage: str = "scope") -> None:
        from argus_skill.life.supervisor._planning_cycle import PlanningCycleMixin

        self._reconcile = PlanningCycleMixin._reconcile_open_ended_planner_waiting.__get__(
            self
        )
        self.config = SimpleNamespace(open_ended=True, continuous_objective="obj")
        self.manager_calls = 0
        self._stage = stage
        self._planner_waits_since_reconciliation = 0
        self._last_planner_wait_reconciliation_key = None
        self.planner_runner = None
        self.skill_store = None
        self.sink = SimpleNamespace(handle_event=None)
        self.events: list[dict] = []
        self.manager_feedback: list[dict] = []

    @staticmethod
    def _waiting_contract_key(contract):
        from argus_skill.life.supervisor._planning_context import PlanningContextMixin

        return PlanningContextMixin._waiting_contract_key(contract)

    def _artifact_root(self):
        from pathlib import Path

        return Path("/nonexistent-for-this-test")

    def _emit(self, event: dict) -> None:
        self.events.append(event)

    def _emit_status(self, text: str) -> None:
        return None

    def _reset_idle_backoff(self) -> None:
        return None

    def _enter_idle_backoff(self) -> float:
        return 0.0

    def _load_planner_waiting_contract_state(self):
        return None

    def _write_planner_waiting_contract_state(self, state) -> None:
        return None

    def _persist_planner_waiting_contract(self, contract):
        # The real one returns None without a recheck_condition, which is the
        # second short-circuit this test exists to hold.
        if str(getattr(contract, "recheck_condition", "") or "").strip():
            return {
                "blocker_fingerprint": contract.blocker_fingerprint,
                "recheck_token": contract.recheck_token,
            }
        return None

    def _persist_manager_planner_feedback(self, **feedback):
        self.manager_feedback.append(feedback)
        return True

    def _deactivate_planner_waiting_contract(self) -> None:
        return None

    def _clear_planner_wait_resolution(self) -> None:
        return None

    def _apply_manager_wait_resolution(self, *a, **k):
        return None

    def _bound_manager(self):
        probe = self

        class _Marker:
            def decide_stage_transition(self, **kwargs):
                probe.manager_calls += 1
                return SimpleNamespace(
                    action="hold",
                    target_stage="scope",
                    reason="held",
                    current_stage="scope",
                    source="manager_llm",
                    diagnostic="",
                    resolves_wait=False,
                )

        return _Marker()


def _reconciles(probe: _Probe, verdict) -> bool:
    """Did reconciliation invoke the composed Manager's stage decision?"""
    probe._reconcile(verdict)
    return probe.manager_calls > 0


def test_an_uncontracted_wait_still_reaches_the_manager() -> None:
    probe = _Probe()

    assert _reconciles(probe, _verdict()) is True
    assert probe.manager_feedback == [
        {
            "stage": "idea",
            "reason": "held",
            "diagnostic": "manager_hold_requires_stage_repair",
        }
    ]


def test_a_wait_with_no_reason_at_all_is_left_alone() -> None:
    """Nothing to deduplicate on, and nothing to tell the Manager either."""
    probe = _Probe()

    result = probe._reconcile(_verdict(waiting_reason="", reason=""))

    assert result == ""


def test_an_operator_gated_wait_is_never_reconciled() -> None:
    """The Manager is not the operator and cannot expand operator scope."""
    probe = _Probe()
    contract = SimpleNamespace(
        blocker_fingerprint="",
        recheck_token="",
        operator_action_required=True,
        stage_reconciliation_required=False,
    )

    result = probe._reconcile(_verdict(waiting_contract=contract))

    assert result == ""


def test_deterministic_subagent_event_wait_never_polls_the_manager() -> None:
    probe = _Probe()
    contract = SimpleNamespace(
        blocker_fingerprint="live-subagents:abc",
        recheck_token="run-1",
        operator_action_required=False,
        stage_reconciliation_required=False,
        wait_mode="event",
        wake_on=("subagent_state",),
    )

    result = probe._reconcile(_verdict(waiting_contract=contract))

    assert result == ""
    assert probe.manager_calls == 0


def test_event_wait_without_wake_source_still_reaches_the_manager() -> None:
    probe = _Probe()
    contract = SimpleNamespace(
        blocker_fingerprint="live-subagents:abc",
        recheck_token="run-1",
        operator_action_required=False,
        stage_reconciliation_required=False,
        wait_mode="event",
        wake_on=(),
        recheck_condition="wait for a real event source",
    )

    probe._reconcile(_verdict(waiting_contract=contract))

    assert probe.manager_calls == 1


@pytest.mark.parametrize(
    "field", ["waiting", "project_done", "new_tasks"]
)
def test_the_existing_preconditions_still_hold(field: str) -> None:
    probe = _Probe()
    overrides = {
        "waiting": {"waiting": False},
        "project_done": {"project_done": True},
        "new_tasks": {"new_tasks": [object()]},
    }[field]

    assert probe._reconcile(_verdict(**overrides)) == ""
