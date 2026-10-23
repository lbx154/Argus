from __future__ import annotations

import json

import pytest

from argus.core.models import ReviewDecision
from argus.core.operator_context import append_directive
from argus.manager import Manager
from argus.manager.directive import clear_active_manager_directive, set_active_manager_directive
from argus.skills.vertical_select import persist_vertical
from tests.hand_off import stage


@pytest.mark.parametrize("structured", [False, True])
def test_stage_manager_receives_current_operator_context_from_state_root(
    optimize_vertical,
    tmp_path, structured,
):
    state = tmp_path / "state"
    work = tmp_path / "work"
    work.mkdir()
    persist_vertical(state, optimize_vertical, workflow_mode="staged")
    (state / "continuous.json").write_text(json.dumps({"objective": "original goal"}))
    directive = "Keep accepted groundwork; repair the actual remaining interface."
    if structured:
        append_directive(
            state, directive, scope="project", lifetime="standing",
            applies_to_roles=["manager"], source="test", expected_revision=0,
        )
        append_directive(
            state, "ENGINEER_ONLY_PRIVATE_NOTE", scope="project", lifetime="standing",
            applies_to_roles=["engineer"], source="test", expected_revision=1,
        )
    else:
        set_active_manager_directive(state, directive)
    set_active_manager_directive(work, "WRONG_WORKTREE_STEERING")
    decide = stage(move="hold")

    manager = Manager(state, runner=object(), execution_workdir=work)
    for _ in range(2):
        decision = manager.decide_stage_transition(
            review=ReviewDecision(
                status="done", reason="One bounded repair accepted.", next_action="",
            ),
            project_root=state, run_exec=decide, open_ended=True,
            continuous_objective="original goal",
        )
        assert decision.action == "hold"
        assert decision.source == "manager_llm"
    assert len(decide.prompts) >= 2
    for prompt in decide.prompts:
        assert directive in prompt
        assert "WRONG_WORKTREE_STEERING" not in prompt
        assert "ENGINEER_ONLY_PRIVATE_NOTE" not in prompt
        assert "operator_context_revision=" in prompt
    assert json.loads((state / ".argus/PIPELINE_STATE.json").read_text())[
        "current_stage"
    ] == "setup"


@pytest.mark.parametrize("retract", [False, True])
def test_changed_operator_context_prevents_stale_stage_commit(optimize_vertical, tmp_path, retract):
    state = tmp_path / "state"
    work = tmp_path / "work"
    work.mkdir()
    persist_vertical(state, optimize_vertical, workflow_mode="staged")
    set_active_manager_directive(state, "Current scope is unchanged.")
    before = (state / ".argus/PIPELINE_STATE.json").read_bytes()

    def decide(prompt, actions):
        if retract:
            clear_active_manager_directive(state)
        else:
            set_active_manager_directive(state, "Reassess the changed requirement.")
        return stage(move="advance", target_stage="measure")(prompt, actions)

    result = Manager(state, runner=object(), execution_workdir=work).decide_stage_transition(
        review=ReviewDecision(status="done", reason="One bounded repair accepted.", next_action=""),
        project_root=state, run_exec=decide, open_ended=True,
    )
    assert result.action == "hold"
    assert result.source == "stale_stage_context_hold"
    assert result.diagnostic == "stage_context_changed"
    assert (state / ".argus/PIPELINE_STATE.json").read_bytes() == before
