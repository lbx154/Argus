"""A finished staged project must be able to leave the stage it finished in.

Testbed run 15 (``s-f0dbba19``) solved the universal-moduli problem end to end:
a reproducible survey over ``1 <= m <= 200``, a both-directions proof that the
universal integers are exactly the divisors of 24, and a Lean 4 file that builds
against Mathlib with no ``sorry`` and no ``axiom`` — recompiled independently
from this repository, exit 0. A ``final_submission`` mission reviewed all of it
and certified it.

Its Manager then emitted, exactly as the stage prompt instructs::

    ACTION=complete
    TARGET_STAGE=scope
    REASON=Reviewer certification establishes the scoped objective and all
    requested dependent phases...

``current_stage`` was ``scope``, so completion was refused: it is only legal at
the last stage. The stage never moved, the project could not end, and the run
joined 11, 12 and 13 in the same place. Run 13's Engineer had found the only
escape available to it — calling ``complete_final_stage`` by hand — which is a
separate defect this repository now quarantines rather than promotes.

An action the prompt tells the model to emit and the machine can only ever
refuse is not a guardrail. When the pipeline's position is the *sole* objection,
the decision becomes a one-step advance; every gate still runs, because
``advance_stage`` validates the stage being left. When anything else also
objected, the completion was wrong on its merits and holds.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from argus_skill.core.models import ReviewDecision
from argus_skill.manager import Manager
from argus_skill.manager.stage_decider import (
    final_stage_completion_blockers,
    stage_position_is_the_only_completion_blocker,
)
from argus_skill.skills.vertical_select import persist_vertical

ORDER = ("research", "plan", "benchmark", "run", "analysis", "draft", "review", "submission")


def _review(status: str = "done") -> ReviewDecision:
    return ReviewDecision(
        status=status,
        reason="Reviewer inspected the evidence and made this judgment.",
        next_action="" if status == "done" else "Continue the work.",
    )


def _decide(tmp_path, *, mission_scope: str, review: ReviewDecision | None = None):
    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    persist_vertical(state_root, "research", workflow_mode="staged")
    decision = Manager(
        project_root=state_root,
        execution_workdir=workdir,
        runner=object(),
    ).decide_stage_transition(
        review=review if review is not None else _review(),
        project_root=state_root,
        mission_scope=mission_scope,
        open_ended=False,
        run_exec=lambda _prompt: SimpleNamespace(
            last_agent_message=(
                '{"action":"complete","target_stage":"research",'
                '"reason":"reviewer certification establishes the objective"}'
            )
        ),
    )
    state = json.loads((state_root / ".argus" / "PIPELINE_STATE.json").read_text())
    return decision, state


def test_a_certified_final_submission_leaves_the_stage_it_finished_in(tmp_path) -> None:
    """Run 15's verdict, executed instead of refused."""
    decision, state = _decide(tmp_path, mission_scope="final_submission")

    assert decision.action == "advance"
    assert decision.diagnostic == "complete_at_nonfinal_advanced"
    assert state["current_stage"] == "plan"


def test_the_managers_own_reason_survives_the_rewrite(tmp_path) -> None:
    """The stage history's only account of why the project moved."""
    decision, _state = _decide(tmp_path, mission_scope="final_submission")

    assert "reviewer certification" in decision.reason.lower()


def test_a_bounded_mission_still_cannot_move_the_project(tmp_path) -> None:
    """Two objections, not one: position *and* no standing to close.

    The rescue is for a completion that was right about everything except where
    it was standing. A bounded mission's completion is not that.
    """
    decision, state = _decide(tmp_path, mission_scope="bounded")

    assert decision.action == "hold"
    assert state["current_stage"] == "research"


def test_an_uncertified_review_still_cannot_move_the_project(tmp_path) -> None:
    decision, state = _decide(
        tmp_path, mission_scope="final_submission", review=_review("continue")
    )

    assert decision.action == "hold"
    assert state["current_stage"] == "research"


def test_publishable_completion_rechecks_publication_scale_artifact(
    tmp_path,
) -> None:
    state_root = tmp_path / "state"
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    persist_vertical(
        state_root,
        "research",
        workflow_mode="staged",
        research_target_level="publishable",
    )
    review = ReviewDecision(
        status="done",
        reason="Reviewer accepted the paper.",
        next_action="",
        research_result={
            "result_class": "verified_new_result",
            "correctness_status": "verified",
            "novelty_status": "verified_new",
            "significance_status": "publishable",
            "statement_fidelity_status": "verified",
            "evidence": ["paper/main.pdf"],
            "limitations": [],
        },
    )

    decision = Manager(
        project_root=state_root,
        execution_workdir=workdir,
        runner=object(),
    ).decide_stage_transition(
        review=review,
        project_root=state_root,
        mission_scope="final_submission",
        open_ended=False,
        run_exec=lambda _prompt: SimpleNamespace(
            last_agent_message=(
                '{"action":"complete","target_stage":"research",'
                '"reason":"reviewer certification establishes the objective"}'
            )
        ),
    )

    assert decision.action == "hold"
    assert "PUBLICATION_SCALE_ASSESSMENT.json" in decision.reason
    assert "ARGUMENT_ORGANIZATION.json" in decision.reason


def test_the_blockers_report_every_refusal_not_just_the_first() -> None:
    """A short-circuiting list cannot tell "only position" from "position too"."""
    blockers = final_stage_completion_blockers(
        _review(),
        current_stage="research",
        stage_order=ORDER,
        vertical="research",
        mission_scope="bounded",
    )

    assert len(blockers) == 2
    assert any("only legal at the final stage" in b for b in blockers)
    assert any("cannot close a" in b for b in blockers)


def test_position_alone_is_recognised_as_the_sole_blocker() -> None:
    blockers = final_stage_completion_blockers(
        _review(),
        current_stage="research",
        stage_order=ORDER,
        vertical="research",
        mission_scope="final_submission",
    )

    assert stage_position_is_the_only_completion_blocker(blockers)


def test_no_blockers_is_not_a_position_problem() -> None:
    """An empty tuple means "no explanation available", never "advance"."""
    assert not stage_position_is_the_only_completion_blocker(())


def test_a_second_blocker_disqualifies_the_rewrite() -> None:
    blockers = final_stage_completion_blockers(
        _review(),
        current_stage="research",
        stage_order=ORDER,
        vertical="research",
        mission_scope="final_submission",
        completion_blocker="the operator has not answered the open question",
    )

    assert len(blockers) == 2
    assert not stage_position_is_the_only_completion_blocker(blockers)


def test_the_prompt_no_longer_invites_completion_from_an_earlier_stage() -> None:
    """The machine is the authority, but the two should not contradict.

    The bullet used to read "COMPLETE at the current stage when its checklist is
    certified ... and every later stage is inapplicable", which is advice to do
    the one thing that could not work.
    """
    from argus_skill.roles.prompts import manager as manager_prompts

    with open(manager_prompts.__file__, encoding="utf-8") as handle:
        text = handle.read()

    assert "COMPLETE only at the final stage of a finite objective" in text
    assert "every later stage " not in text
