"""Regression test: a refused completion must say which check refused.

``final_stage_completion_decision`` answers yes-or-no across four independent
checks — stage position, external gate, mission scope, reviewer certification —
and returns a bare ``None`` for every no. Its one caller turned that into a
single sentence, "Manager completion rejected by the project completion
contract", identical for all four.

That sentence is the whole feedback loop. Testbed run 13 (``s-d9ea298f``) hit
the *stage position* check with the problem fully solved: a reproducible search
program, a both-directions proof of the characterization, and a Lean 4 build
containing ``universal_24`` and ``not_universal_48`` with no ``sorry`` and no
``axiom``, all reviewer-certified. The actual answer was "you are at ``scope``;
completion is only legal at ``review``". What the Planner did with the opaque
version was queue a mission titled "Certify the scope-stage Goal Gate", whose
objective included "record the missing route/ledger state or equivalent gate
metadata if required by the workflow" — inventing gate bookkeeping to explain a
refusal it had no way to read.

Citations:
- argus_skill/manager/stage_decider.py — ``final_stage_completion_blockers``
- argus_skill/manager/_stage_ops.py — the ``manager_completion_rejected`` HOLD
"""

from __future__ import annotations

from types import SimpleNamespace

from argus_skill.manager.stage_decider import (
    final_stage_completion_blockers,
    final_stage_completion_decision,
)

STAGES = ["scope", "solve", "review"]


def _review(status: str = "done"):
    return SimpleNamespace(status=status)


def test_a_non_final_stage_says_so_and_names_what_remains() -> None:
    """Run 13's exact situation."""
    blockers = final_stage_completion_blockers(
        _review(),
        current_stage="scope",
        stage_order=STAGES,
        vertical="math",
    )

    assert blockers
    joined = " ".join(blockers)
    assert "final stage" in joined
    assert "'review'" in joined
    assert "solve" in joined, "the operator needs to know what is still ahead"


def test_an_external_gate_blocker_is_passed_through_verbatim() -> None:
    blockers = final_stage_completion_blockers(
        _review(),
        current_stage="review",
        stage_order=STAGES,
        vertical="math",
        completion_blocker="benchmark regression is still open",
    )

    assert blockers == ("benchmark regression is still open",)


def test_a_bounded_mission_is_told_it_lacks_standing() -> None:
    """Only applies where the vertical's gate is ``certified``.

    ``research`` is such a vertical; ``math`` (gate ``none``) deliberately lets
    any mission envelope carry the verdict, so this check never fires there.
    """
    blockers = final_stage_completion_blockers(
        _review(),
        current_stage="submission",
        stage_order=["research", "submission"],
        vertical="research",
        mission_scope="bounded",
    )

    assert blockers
    joined = " ".join(blockers)
    assert "bounded" in joined
    assert "final_submission" in joined


def test_an_unknown_stage_is_named_rather_than_swallowed() -> None:
    blockers = final_stage_completion_blockers(
        _review(),
        current_stage="publication",
        stage_order=STAGES,
        vertical="math",
    )

    assert blockers
    assert "publication" in " ".join(blockers)


def test_the_blockers_agree_with_the_decision() -> None:
    """The two functions duplicate the checks; they must not disagree.

    An empty blocker tuple beside a ``None`` decision would be a silent
    regression back to the unexplained refusal this exists to remove.
    """
    cases = [
        {"current_stage": "scope", "stage_order": STAGES, "vertical": "math"},
        {
            "current_stage": "review",
            "stage_order": STAGES,
            "vertical": "math",
            "completion_blocker": "gate open",
        },
        {
            "current_stage": "submission",
            "stage_order": ["research", "submission"],
            "vertical": "research",
            "mission_scope": "bounded",
        },
        {"current_stage": "publication", "stage_order": STAGES, "vertical": "math"},
    ]
    for kwargs in cases:
        decision = final_stage_completion_decision(_review(), **kwargs)
        blockers = final_stage_completion_blockers(_review(), **kwargs)
        assert decision is None, kwargs
        assert blockers, kwargs


def test_nothing_blocks_a_decision_that_is_allowed() -> None:
    kwargs = {
        "current_stage": "review",
        "stage_order": STAGES,
        "vertical": "math",
        "mission_scope": "project",
    }
    decision = final_stage_completion_decision(_review(), **kwargs)
    blockers = final_stage_completion_blockers(_review(), **kwargs)

    assert (decision is None) == bool(blockers)


def test_research_final_review_done_certifies_regardless_of_result_grades() -> None:
    """For the paper vertical the Reviewer's ``done`` on the final review is
    the certification. Grades such as ``finite_verification`` or novelty
    ``unverified`` describe the verdict; they do not overrule it."""
    from argus_skill.manager.stage_decider import _review_certifies_completion

    review = SimpleNamespace(
        status="done",
        research_result={
            "result_class": "finite_verification",
            "correctness_status": "verified",
            "novelty_status": "unverified",
            "significance_status": "publishable",
            "statement_fidelity_status": "verified",
            "evidence": ["independent recomputation matched every headline number"],
            "limitations": [],
        },
    )
    assert (
        _review_certifies_completion(
            review,
            vertical="research",
            mission_scope="final_submission",
            research_target_level="publishable",
        )
        == ""
    )
    # Formal verticals keep the grade check.
    assert (
        _review_certifies_completion(
            review,
            vertical="math",
            mission_scope="final_submission",
            research_target_level="publishable",
        )
        == "result_class_below_publishable:finite_verification"
    )
