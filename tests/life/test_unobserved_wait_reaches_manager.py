"""A wait the host cannot observe is where the Manager review matters most.

Measured on 2026-07-26 in a captured project event log: a kernel_engineering
campaign emitted several consecutive `life.planner.waiting` verdicts with no
usable contract and produced zero Manager stage reviews. Thirteen provider
calls, no progress, and the Planner had already said exactly what was wrong:

    "Scope checklist is satisfied, but only the Manager may advance
     current_stage from scope to environment."

The one authority that could act was never asked. Now every Planner wait is
bound by the host; one it cannot watch climbs to the Manager, once, in bounded
and open-ended campaigns alike. The Manager still decides HOLD versus ROLLBACK,
and nothing here reads the research.
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

    def __init__(self, *, stage: str = "scope", open_ended: bool = True) -> None:
        from argus.life.supervisor._planning_context import PlanningContextMixin
        from argus.life.supervisor._planning_cycle import PlanningCycleMixin

        self._reconcile = PlanningCycleMixin._reconcile_open_ended_planner_waiting.__get__(
            self
        )
        for name in (
            "_bind_planner_wait", "_project_paths_named_in", "_confined_planner_wait_paths",
        ):
            setattr(self, name, getattr(PlanningContextMixin, name).__get__(self))
        self.config = SimpleNamespace(open_ended=open_ended, continuous_objective="obj")
        self.manager = object()
        self.manager_calls = 0
        self.manager_open_ended = None
        self._stage = stage
        self._last_planner_wait_reconciliation_key = None
        self.planner_runner = None
        self.skill_store = None
        self.sink = SimpleNamespace(handle_event=None)
        self.events: list[dict] = []
        self.manager_feedback: list[dict] = []

    @staticmethod
    def _waiting_contract_key(contract):
        from argus.life.supervisor._planning_context import PlanningContextMixin

        return PlanningContextMixin._waiting_contract_key(contract)

    def _artifact_root(self):
        from pathlib import Path

        return Path("/nonexistent-for-this-test")

    _project_workdir = _artifact_root

    def _waitable_subagent_jobs(self):
        return []

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
                probe.manager_open_ended = kwargs["open_ended"]
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


def _wait(condition: str = "only the Manager may advance current_stage", **kw):
    from argus.planner import WaitingContract

    return WaitingContract(recheck_condition=condition, **kw)


@pytest.mark.parametrize("open_ended", [False, True])
def test_an_unobserved_wait_reaches_the_manager(open_ended: bool) -> None:
    probe = _Probe(open_ended=open_ended)

    assert _reconciles(probe, _verdict(waiting_contract=_wait())) is True
    assert probe.manager_feedback == [
        {
            "stage": "idea",
            "reason": "held",
            "diagnostic": "manager_hold_requires_stage_repair",
        }
    ]


def test_the_same_wait_is_reconciled_once() -> None:
    """Returning the same wait is not news: no count re-adjudicates it."""
    probe = _Probe(open_ended=True)
    contract = _wait(
        "results/metrics.json is written",
        blocker_fingerprint="artifacts:results/metrics.json",
        recheck_token="results/metrics.json is written",
        wake_on=("artifact_revision",),
        watched_paths=("results/metrics.json",),
    )

    for _ in range(5):
        probe._reconcile(_verdict(waiting_contract=contract))

    assert probe.manager_calls == 1


def test_a_wait_with_no_sentence_at_all_is_left_alone() -> None:
    """Nothing to deduplicate on, and nothing to tell the Manager either."""
    probe = _Probe()

    assert probe._reconcile(_verdict(waiting_contract=_wait(""))) == ""
    assert probe.manager_calls == 0


def test_an_operator_gated_wait_is_never_reconciled() -> None:
    """The operator, or the Manager in their place, decides it -- not stage review."""
    probe = _Probe()

    result = probe._reconcile(
        _verdict(waiting_contract=_wait(operator_action_required=True))
    )

    assert result == ""
    assert probe.manager_calls == 0


def test_deterministic_subagent_event_wait_never_polls_the_manager() -> None:
    probe = _Probe()
    contract = _wait(
        blocker_fingerprint="live-subagents:abc",
        recheck_token="abc@run-1",
        wake_on=("subagent_state",),
    )

    assert probe._reconcile(_verdict(waiting_contract=contract)) == ""
    assert probe.manager_calls == 0


def test_bounded_explicit_stage_wait_reaches_the_manager() -> None:
    probe = _Probe(open_ended=False)
    contract = _wait(
        "Manager must reconcile the current stage",
        stage_reconciliation_required=True,
    )

    result = probe._reconcile(_verdict(waiting_contract=contract))

    assert result == "hold"
    assert probe.manager_calls == 1
    assert probe.manager_feedback[0]["diagnostic"] == "manager_hold_requires_stage_repair"
    assert probe.manager_open_ended is False
    assert probe.events[0]["trigger"] == "planner_waiting_reconciliation"
    assert probe.events[0]["action"] == "hold"


@pytest.mark.parametrize("observed", [False, True])
def test_a_bounded_wait_the_host_watches_does_not_reach_the_manager(observed: bool) -> None:
    """A wait bound to a file the host watches wakes by itself; no contract, no wait."""
    probe = _Probe(open_ended=False)
    contract = _wait(
        "results/metrics.json is written",
        blocker_fingerprint="artifacts:results/metrics.json",
        recheck_token="results/metrics.json is written",
        wake_on=("artifact_revision",),
        watched_paths=("results/metrics.json",),
    ) if observed else None

    assert probe._reconcile(_verdict(waiting_contract=contract)) == ""
    assert probe.manager_calls == 0


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("workflow_mode", ["staged", "direct"])
def test_real_manager_receives_bounded_wait_without_advancing_without_review(
    tmp_path, explicit: bool, workflow_mode: str,
) -> None:
    import json

    from argus.manager import Manager
    from argus.planner import WaitingContract
    from argus.skills.vertical_select import persist_vertical

    state = tmp_path / "state"
    work = tmp_path / "work"
    work.mkdir()
    persist_vertical(state, "math_synth", workflow_mode=workflow_mode)
    before = (state / ".argus/PIPELINE_STATE.json").read_bytes()
    prompts = []

    def decide(prompt):
        prompts.append(prompt)
        return SimpleNamespace(last_agent_message=json.dumps({
            "action": "advance", "target_stage": "measure",
            "reason": "The Planner requested stage reconciliation.",
        }))

    verdict = _verdict(waiting_contract=WaitingContract(
        recheck_condition="Manager must decide the remaining stage work",
        stage_reconciliation_required=explicit,
    ))
    result = Manager(state, runner=object(), execution_workdir=work).decide_stage_transition(
        project_root=state, planner_verdict=verdict, run_exec=decide,
        open_ended=False,
    )

    assert bool(prompts) is explicit
    assert result.action == "hold"
    if explicit:
        assert result.diagnostic == "planner_wait_advance_rejected"
    else:
        assert result.source == "no_review_hold"
    assert (state / ".argus/PIPELINE_STATE.json").read_bytes() == before


@pytest.mark.parametrize("open_ended", [False, True])
def test_explicit_stage_wait_cannot_override_operator_gate(open_ended: bool) -> None:
    from argus.planner import WaitingContract

    probe = _Probe(open_ended=open_ended)
    contract = WaitingContract(
        recheck_condition="operator authorization is required",
        stage_reconciliation_required=True,
        operator_action_required=True,
    )

    assert probe._reconcile(_verdict(waiting_contract=contract)) == ""
    assert probe.manager_calls == 0


@pytest.mark.parametrize(
    "field", ["waiting", "project_done", "new_tasks"]
)
@pytest.mark.parametrize("open_ended", [False, True])
def test_the_existing_preconditions_still_hold(field: str, open_ended: bool) -> None:
    from argus.planner import WaitingContract

    probe = _Probe(open_ended=open_ended)
    overrides = {
        "waiting": {"waiting": False},
        "project_done": {"project_done": True},
        "new_tasks": {"new_tasks": [object()]},
    }[field]

    contract = WaitingContract(
        recheck_condition="Manager stage reconciliation",
        stage_reconciliation_required=True,
    )

    assert probe._reconcile(_verdict(waiting_contract=contract, **overrides)) == ""
    assert probe.manager_calls == 0


def _file_wait():
    from argus.planner import WaitingContract

    return WaitingContract(
        blocker_fingerprint="results-file",
        recheck_condition="results/metrics.json is written",
        recheck_token="tok-1",
        wake_on=("artifact_revision",),
        watched_paths=("results/metrics.json",),
    )


def test_an_unchanged_wait_gets_one_manager_ruling() -> None:
    """Re-asking every few idle cycles was a Manager call every twenty minutes."""
    probe = _Probe()
    verdict = _verdict(waiting_contract=_file_wait())

    for _ in range(200):
        probe._reconcile(verdict)

    assert probe.manager_calls == 1
