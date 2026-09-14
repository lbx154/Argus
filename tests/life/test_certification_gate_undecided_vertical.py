"""An undecided project must not inherit research's certified gate.

``_effective_final_certification_gate`` AND-s the operator's configuration with
the *active vertical's* completion gate, so a ``math`` or ``speedrun`` mission —
which has no submission package to certify — can accept ``project_done``
straight from the run loop instead of waiting forever for a certification that
will never be issued.

It resolved that vertical with ``resolve_vertical``, whose contract is not what
the call site assumed. The code caught ``VerticalResolutionError`` and
documented the catch as "we treat 'no vertical yet' as 'gate not satisfied'…
it is NOT a silent default to research". ``resolve_vertical`` does not raise
for an undecided project. It logs a warning and returns ``research``, whose
completion gate *is* ``certified`` — so the branch was dead and the fallback it
was written to avoid was the one actually taken.

Nothing burned, because the wrong answer was masked twice: every production
caller passes ``self._artifact_root()`` (the session state root, which does
carry the Manager's decision), and ``config.final_certification_gate`` is
itself computed by ``_final_certification_for_project_root`` from a *persisted*
certified vertical, so it is already ``False`` for math and materials. Two
independent guards, and the predicate between them said the opposite of what
its own docstring claimed. These tests pin the claim directly, on the
predicate, so it stops depending on both.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus_skill.life.supervisor._planning_context import PlanningContextMixin
from argus_skill.skills.vertical_select import (
    resolve_vertical,
    resolve_vertical_if_decided,
)
from argus_skill.verticals._base import load_vertical_contract

CERTIFIED = "research"
NOT_CERTIFIED = ("math", "math_synth", "kernel_engineering", "software")


class _Harness(PlanningContextMixin):
    def __init__(self, root, *, gate: bool = True) -> None:
        self._root = root
        self.config = SimpleNamespace(final_certification_gate=gate)

    def _artifact_root(self):
        return self._root


def test_the_premise_that_made_this_wrong_still_holds(tmp_path) -> None:
    """Guard the trap: the fallback is silent, so the fix must not be reverted.

    If ``resolve_vertical`` ever starts raising for an undecided root, the
    original ``try/except`` becomes correct and this whole file is moot.
    """
    assert resolve_vertical(tmp_path) == CERTIFIED
    assert resolve_vertical_if_decided(tmp_path) is None
    assert load_vertical_contract(CERTIFIED).completion_gate == "certified"


def test_an_undecided_project_is_not_at_its_final_gate(tmp_path) -> None:
    """No Manager decision on this root means the gate cannot apply yet."""
    assert _Harness(tmp_path)._effective_final_certification_gate(tmp_path) is False


@pytest.mark.parametrize("vertical", NOT_CERTIFIED)
def test_a_vertical_without_a_certified_gate_stays_ungated(
    tmp_path, vertical
) -> None:
    """``math_synth`` (a metric vertical) is the shape that, gated, wedges forever."""
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, vertical)

    assert _Harness(tmp_path)._effective_final_certification_gate(tmp_path) is False


def test_research_keeps_its_gate(tmp_path) -> None:
    """The fix must change nothing for the vertical the gate exists for."""
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, CERTIFIED)

    assert _Harness(tmp_path)._effective_final_certification_gate(tmp_path) is True


def test_the_operator_switch_still_wins_over_a_certified_vertical(tmp_path) -> None:
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, CERTIFIED)

    assert (
        _Harness(tmp_path, gate=False)._effective_final_certification_gate(tmp_path)
        is False
    )


def test_a_workdir_that_records_no_decision_does_not_gate(tmp_path) -> None:
    """The two-root split is what makes this reachable at all.

    Testbed runs 15 and 16 kept the Manager's vertical in the session state
    root and ran in a separate repository workdir that holds only the adopted
    objective. Any caller handed the workdir sees an undecided project.
    """
    from argus_skill.skills.vertical_select import persist_vertical

    state_root = tmp_path / "s-ed5b69fc"
    workdir = tmp_path / "argus-testbed-univ24-r16"
    state_root.mkdir()
    workdir.mkdir()
    persist_vertical(state_root, "math")

    harness = _Harness(state_root)

    assert harness._effective_final_certification_gate(workdir) is False
    assert harness._effective_final_certification_gate(state_root) is False


def test_the_scope_predicate_inherits_the_same_answer(tmp_path) -> None:
    """``_final_submission_scope_applies`` short-circuits on this predicate."""
    assert _Harness(tmp_path)._final_submission_scope_applies(tmp_path) is False
