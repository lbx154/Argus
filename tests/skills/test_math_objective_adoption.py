"""Regression test: a math project created through the product can complete.

``math_objective_mode`` gates *every* math stage — ``scope`` included, because
the objective check in ``verticals.math.stages`` runs before the stage
dispatch. Until this wiring landed, ``set_objective`` had exactly one
production entry point: the argparse CLI at the bottom of
``verticals/math/objective_mode.py``. Nothing in the Manager, front door,
webapi, daemon or any UI called it, and no agent prompt named the concept.

So a math project created through the real front door could not close a single
stage, and the only remedy was shell access to the host to run a module CLI.
"The operator decides" had been implemented as "an operator with shell access
decides". Every testbed run that appeared to work did so because a
development-time launch harness wrote the mode before the Manager was ever
invoked; scanning every recorded run's ``events.jsonl`` for an in-product
objective write returned nothing.

The fix is transcription, not choice: the operator's own request becomes the
goal, recorded as ``transcribed_from_request`` so it is distinguishable on
disk, and always under the *stronger* of the two bars so nothing is certified
against a bar looser than the operator would have picked.

Citations:
- argus/verticals/math/objective_mode.py — ``adopt_operator_objective``
- argus/core/vertical_contract.py — ``operator_objective_adopter``
- argus/manager/_vertical_ops.py — ``_adopt_operator_objective``
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.core.vertical_contract import VerticalContract, VerticalContractError
from argus.verticals._base import (
    load_vertical,
    vertical_adopt_operator_objective,
)
from argus.verticals.math.objective_mode import (
    SOURCE_KEY,
    SOURCE_OPERATOR,
    SOURCE_TRANSCRIBED,
    resolve_objective,
    set_objective,
)

REQUEST = "Prove that every group of order 24 has a normal Sylow subgroup"


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".argus").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "math", "current_stage": "scope"}) + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _state(root: Path) -> dict:
    return json.loads(
        (root / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )


def test_an_unset_objective_is_adopted_from_the_request(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert resolve_objective(root).resolved is False

    ran = vertical_adopt_operator_objective(
        load_vertical("math"), project_root=root, request=REQUEST
    )

    assert ran is True
    objective = resolve_objective(root)
    assert objective.resolved is True
    assert objective.mode == "targeted"
    assert objective.goal == REQUEST
    assert _state(root)[SOURCE_KEY] == SOURCE_TRANSCRIBED


def test_an_operator_choice_is_never_overwritten(tmp_path: Path) -> None:
    """The whole point of the module is that the operator's choice is final."""
    root = _project(tmp_path)
    set_objective(root, mode="exploratory")

    vertical_adopt_operator_objective(
        load_vertical("math"), project_root=root, request=REQUEST
    )

    objective = resolve_objective(root)
    assert objective.mode == "exploratory"
    assert objective.goal == ""
    assert _state(root)[SOURCE_KEY] == SOURCE_OPERATOR


def test_adoption_is_idempotent(tmp_path: Path) -> None:
    """Re-dispatching a mission must not rewrite the goal mid-project."""
    root = _project(tmp_path)
    vertical_adopt_operator_objective(
        load_vertical("math"), project_root=root, request=REQUEST
    )
    vertical_adopt_operator_objective(
        load_vertical("math"), project_root=root, request="something else entirely"
    )

    assert resolve_objective(root).goal == REQUEST


def test_an_empty_request_adopts_nothing(tmp_path: Path) -> None:
    """A blank goal would pass the mode gate and fail the identity check later."""
    root = _project(tmp_path)

    vertical_adopt_operator_objective(
        load_vertical("math"), project_root=root, request="   \n  "
    )

    assert resolve_objective(root).resolved is False
    assert "math_objective_mode" not in _state(root)


def test_adoption_unblocks_the_scope_stage(tmp_path: Path) -> None:
    """The reproduction, end to end.

    An unset mode blocks ``scope`` — the first stage — so the project was
    stalled before it produced anything. The adopted objective clears exactly
    that issue.
    """
    from argus.verticals.math.stages import stage_completion_issues

    root = _project(tmp_path)
    before = stage_completion_issues("scope", root)
    assert any("objective mode" in issue for issue in before), before

    vertical_adopt_operator_objective(
        load_vertical("math"), project_root=root, request=REQUEST
    )

    after = stage_completion_issues("scope", root)
    assert not any("objective mode" in issue for issue in after), after


@pytest.mark.parametrize("vertical", ["research", "math_synth"])
def test_verticals_without_an_adopter_are_untouched(
    tmp_path: Path, vertical: str
) -> None:
    """The hook is optional: a vertical with nothing to choose declares none."""
    root = _project(tmp_path)

    assert (
        vertical_adopt_operator_objective(
            load_vertical(vertical), project_root=root, request=REQUEST
        )
        is False
    )
    assert "math_objective_mode" not in _state(root)


def test_a_non_callable_adopter_is_rejected_by_the_contract() -> None:
    """Same fail-closed shape as the other optional provider hooks."""
    from types import SimpleNamespace

    from argus.core.vertical_contract import vertical_contract
    from argus.skills.stage_machine import ChecklistItem

    provider = SimpleNamespace(
        CHECKLIST_STAGE_ORDER=("scope",),
        CHECKLIST_ITEMS={
            "scope": (ChecklistItem("scope.output", "Verify scope", "evidence"),)
        },
        completion_gate="none",
        adopt_operator_objective="not callable",
    )

    with pytest.raises(VerticalContractError, match="operator objective adopter"):
        vertical_contract("stub", provider)


def test_the_contract_no_ops_without_an_adopter(tmp_path: Path) -> None:
    contract = VerticalContract(
        name="stub",
        stage_order=("scope",),
        checklist_items={},
        completion_gate="none",
    )

    assert contract.adopt_operator_objective(tmp_path, REQUEST) is False


def test_the_manager_adopts_on_committing_a_math_vertical(tmp_path: Path) -> None:
    """The wiring, not the primitive.

    ``adopt_operator_objective`` was correct in isolation long before this
    landed — it had no caller, which is the entire defect. This test fails if
    the hook is ever unhooked from the division path.
    """
    from argus.manager import Manager
    from argus.manager.domain_author import VerticalDecision

    manager = Manager(project_root=tmp_path)
    decision = VerticalDecision(
        choice="existing",
        vertical="math",
        execution_task=REQUEST,
    )

    manager.commit_vertical_decision(REQUEST, decision)

    state = _state(tmp_path)
    assert state["math_objective_mode"] == "targeted"
    assert state["math_goal"] == REQUEST
    assert state[SOURCE_KEY] == SOURCE_TRANSCRIBED


def test_the_manager_adopts_into_the_execution_workdir_too(tmp_path: Path) -> None:
    """The layout the daemon actually runs, and the one that matters.

    Pipeline state lives under ``~/.argus-skill/projects/<sid>``; the math
    artifacts live in the operator's repo. ``_ensure_stage_completion`` calls
    the vertical validator with ``evidence_root or project_root``, so the
    objective the gate reads is the *workdir* copy. Adopting only into the
    project root would leave the gate exactly as unsatisfiable as before —
    which is how every recorded testbed run reached its stage gate.
    """
    from argus.manager import Manager
    from argus.manager.domain_author import VerticalDecision

    project_root = tmp_path / "life"
    workdir = tmp_path / "repo"
    project_root.mkdir()
    workdir.mkdir()

    manager = Manager(project_root=project_root, execution_workdir=workdir)
    manager.commit_vertical_decision(
        REQUEST,
        VerticalDecision(choice="existing", vertical="math", execution_task=REQUEST),
    )

    for root in (project_root, workdir):
        state = _state(root)
        assert state["math_objective_mode"] == "targeted", root
        assert state["math_goal"] == REQUEST, root

    from argus.verticals.math.stages import stage_completion_issues

    issues = stage_completion_issues("scope", workdir)
    assert not any("objective mode" in issue for issue in issues), issues


def test_the_manager_leaves_other_verticals_alone(tmp_path: Path) -> None:
    from argus.manager import Manager
    from argus.manager.domain_author import VerticalDecision

    manager = Manager(project_root=tmp_path)
    decision = VerticalDecision(
        choice="existing",
        vertical="research",
        execution_task=REQUEST,
    )

    manager.commit_vertical_decision(REQUEST, decision)

    assert "math_objective_mode" not in _state(tmp_path)


def test_an_adopter_failure_does_not_break_the_division(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-open: the hook runs inside ``_restore_files_on_error``.

    Raising here would roll back a correctly persisted vertical decision over
    an optional convenience, and a learned data domain has no vertical module
    at all — a lookup failure, not a defect.
    """
    from argus.manager import Manager, _vertical_ops
    from argus.manager.domain_author import VerticalDecision
    from argus.verticals import _base

    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("adopter exploded")

    monkeypatch.setattr(_base, "vertical_adopt_operator_objective", _boom)
    assert _vertical_ops is not None

    manager = Manager(project_root=tmp_path)
    division = manager.commit_vertical_decision(
        REQUEST,
        VerticalDecision(choice="existing", vertical="math", execution_task=REQUEST),
    )

    assert division.vertical == "math"
    assert _state(tmp_path)["vertical"] == "math"
    assert "math_objective_mode" not in _state(tmp_path)

