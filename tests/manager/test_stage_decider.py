from __future__ import annotations

import pytest

from argus.manager.stage_decider import stage_check

ORDER = ("research", "plan", "benchmark", "run", "analysis", "draft", "review", "submission")


def _check(to: str = "team", current: str = "research", allow_done: bool = False, **fields):
    payload = {"to": to, "summary": "The reviewed evidence decides it.", **fields}
    return stage_check(payload, current_stage=current, stage_order=ORDER, allow_done=allow_done)


def test_rollback_goes_only_to_an_earlier_stage() -> None:
    assert _check(current="run", move="rollback", target_stage="plan").action == "rollback"
    with pytest.raises(ValueError, match="no stage to move to"):
        _check(move="rollback", target_stage="plan")


def test_done_only_at_the_final_stage_or_in_direct_mode() -> None:
    with pytest.raises(ValueError, match="only at the final stage `submission`"):
        _check("done")
    assert _check("done", allow_done=True).action == "complete"
    assert _check("done", current="submission").action == "complete"


def test_the_team_route_names_its_move() -> None:
    with pytest.raises(ValueError, match="to=team needs move"):
        _check()
    assert _check(move="hold").target_stage == "research"


def test_no_second_machine_value_guard_overrides_manager() -> None:
    """The Manager's parsed decision is what reaches disk.

    This used to be pinned by calling ``enforce_scientific_stage_guard`` and
    asserting it returned its input — but that function had been reduced to
    ``_ = review, current_stage; return decision``, an identity function whose
    name still promised a guard. It was deleted; two live call sites went with
    it. The property it stood for is now pinned structurally instead: the write
    path does not receive the reviewer verdict at all, so there is nowhere for a
    second machine gate to reappear without that being visible in the signature.
    """
    import inspect

    from argus.manager import stage_decider
    from argus.manager._stage_ops import _StageDecisionMixin

    assert not hasattr(stage_decider, "enforce_scientific_stage_guard")

    params = set(inspect.signature(_StageDecisionMixin._apply_stage_decision_to_disk).parameters)
    assert params == {"self", "decision", "cur", "root", "source"}
