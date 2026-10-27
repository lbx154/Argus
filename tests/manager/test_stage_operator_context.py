from __future__ import annotations

import pytest

from argus.core.models import ReviewDecision
from argus.manager import Manager
from argus.manager.directive import clear_active_manager_directive, set_active_manager_directive
from argus.skills.vertical_select import persist_vertical


@pytest.mark.parametrize("retract", [False, True])
def test_changed_operator_context_prevents_stale_stage_commit(optimize_vertical, tmp_path, retract, monkeypatch):
    state = tmp_path / "state"
    work = tmp_path / "work"
    work.mkdir()
    persist_vertical(state, optimize_vertical, workflow_mode="staged")
    set_active_manager_directive(state, "Current scope is unchanged.")
    before = (state / ".argus/PIPELINE_STATE.json").read_bytes()
    gate = Manager._gate_decision

    def decide(self, *args, **kwargs):
        # The operator speaks while the gate reads the evidence.
        if retract:
            clear_active_manager_directive(state)
        else:
            set_active_manager_directive(state, "Reassess the changed requirement.")
        return gate(self, *args, **kwargs)

    monkeypatch.setattr(Manager, "_gate_decision", decide)
    result = Manager(state, runner=object(), execution_workdir=work).decide_stage_transition(
        review=ReviewDecision(status="done", reason="One bounded repair accepted.", next_action=""),
        project_root=state, open_ended=True, stage_closing=True,
    )
    assert result.action == "hold"
    assert result.source == "stale_stage_context_hold"
    assert result.diagnostic == "stage_context_changed"
    assert (state / ".argus/PIPELINE_STATE.json").read_bytes() == before
