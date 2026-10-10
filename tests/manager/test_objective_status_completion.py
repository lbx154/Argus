"""A project is never certified complete on an approval that says the objective is not met.

ab1009 arm E, freight-dispatch-shift: the Planner narrowed mission 15 to a
local check, the Reviewer approved it while writing that it "does not
establish completion of the broader production dispatch objective", and the
Manager completed the project deterministically with no residual risk. The
Reviewer's judgment about the whole objective now travels as a structured
field on approve_review, and completion honours it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from argus.core.models import ReviewDecision
from argus.life.context_packet import record_reviewed_handoff
from argus.manager import Manager
from argus.manager.stage_decider import (
    final_stage_completion_blockers,
    final_stage_completion_decision,
)
from argus.reviewer.tools import ReviewActions
from argus.skills.vertical_select import persist_vertical

_GAP = "Live feed authentication and multi-cutoff production plans remain unverified."


def _review(**changes) -> ReviewDecision:
    values = {
        "status": "done",
        "reason": "Compilation and the labelled fixture suite pass.",
        "next_action": "",
    }
    values.update(changes)
    return ReviewDecision(**values)


def _manager(tmp_path):
    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir(parents=True)
    persist_vertical(state_root, "software", workflow_mode="direct")
    return Manager(project_root=state_root, execution_workdir=workdir, runner=object()), state_root


def _pipeline(root):
    return json.loads((root / ".argus" / "PIPELINE_STATE.json").read_text())


# --- the Reviewer states it ------------------------------------------------


def test_approve_review_offers_the_objective_judgment() -> None:
    schema = {tool["name"]: tool for tool in ReviewActions().tools}["approve_review"]["inputSchema"]
    assert schema["properties"]["objective_status"]["enum"] == ["met", "partial", "not_met"]
    assert "objective_gap" in schema["properties"]
    # Other verdicts do not carry it.
    assert "objective_status" not in {
        tool["name"]: tool for tool in ReviewActions().tools
    }["revise_review"]["inputSchema"]["properties"]


@pytest.mark.parametrize("status", ["partial", "not_met"])
def test_an_unmet_objective_must_name_what_remains(status) -> None:
    with pytest.raises(ValueError, match="objective_gap"):
        ReviewActions().dispatch("approve_review", {"review": "Local checks pass.", "objective_status": status})
    actions = ReviewActions()
    actions.dispatch("approve_review", {
        "review": "Local checks pass.", "objective_status": status, "objective_gap": _GAP,
    })
    assert actions.decision.status == "done"
    assert (actions.decision.objective_status, actions.decision.objective_gap) == (status, _GAP)
    payload = actions.decision.to_event_payload()
    assert payload["objective_status"] == status and payload["objective_gap"] == _GAP


def test_met_is_only_what_the_reviewer_says() -> None:
    met = ReviewActions()
    met.dispatch("approve_review", {"review": "All of it checked.", "objective_status": "met", "objective_gap": "x"})
    assert (met.decision.objective_status, met.decision.objective_gap) == ("met", "")
    unstated = ReviewActions()
    unstated.dispatch("approve_review", {"review": "Checked."})
    assert unstated.decision.objective_status == ""


# --- completion honours it -------------------------------------------------


@pytest.mark.parametrize("status", ["partial", "not_met"])
def test_completion_contract_refuses_an_unmet_objective(status) -> None:
    review = _review(objective_status=status, objective_gap=_GAP)
    kwargs = dict(current_stage="delivery", stage_order=["delivery"], vertical="software",
                  allow_early_completion=True)
    assert final_stage_completion_decision(review, **kwargs) is None
    (blocker,) = final_stage_completion_blockers(review, **kwargs)
    assert "objective" in blocker and _GAP in blocker
    assert final_stage_completion_decision(_review(objective_status="met"), **kwargs) is not None
    assert final_stage_completion_decision(_review(), **kwargs) is not None


def test_a_narrowed_increment_approval_does_not_complete_the_project(tmp_path) -> None:
    manager, state_root = _manager(tmp_path)
    calls: list[str] = []

    def manager_model(prompt: str):
        # Even a Manager that votes to complete is held by the contract.
        calls.append(prompt)
        return SimpleNamespace(last_agent_message=(
            '{"action":"complete","target_stage":"delivery",'
            '"reason":"Reviewer accepted the direct task"}'
        ))

    decision = manager.decide_stage_transition(
        review=_review(objective_status="partial", objective_gap=_GAP),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=manager_model,
    )

    assert decision.action == "hold"
    assert decision.diagnostic == "manager_completion_rejected"
    assert _GAP in decision.reason
    state = _pipeline(state_root)
    assert all(
        row.get("status") != "done" for row in (state.get("stages") or {}).values()
    )


def test_an_approval_that_meets_the_objective_still_completes_deterministically(tmp_path) -> None:
    manager, state_root = _manager(tmp_path)

    def manager_model(prompt: str):
        raise AssertionError("a clean direct approval must not call the Manager model")

    decision = manager.decide_stage_transition(
        review=_review(objective_status="met"),
        project_root=state_root,
        mission_scope="bounded",
        stage_closing=True,
        run_exec=manager_model,
    )

    assert decision.action == "complete"
    assert decision.source == "manager_deterministic"


def test_the_judgment_survives_the_reviewed_handoff(tmp_path) -> None:
    mission = tmp_path / "handoffs" / "m1" / "mission.json"
    mission.parent.mkdir(parents=True)
    mission.write_text(json.dumps({"kind": "mission_context", "mission_id": "m1"}), encoding="utf-8")
    path = record_reviewed_handoff(
        mission_context_path=mission, round_index=1, engineer_summary="ran fixtures",
        review=_review(objective_status="not_met", objective_gap=_GAP), checkpoint_path=None,
    )
    review = json.loads(path.read_text(encoding="utf-8"))["review"]
    assert (review["objective_status"], review["objective_gap"]) == ("not_met", _GAP)
