"""The completion gate reads the claim ledger.

``proof_ledger`` derives ``closed_kernel`` and ``conditional_kernel``, and
until this wiring nothing consulted the answer: a stage could finish with a
ledger that contradicted itself. These tests pin the seam and, deliberately,
its limits — the gate blocks on *structural* defects and stays out of the
question of whether the mathematics is finished.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.proof_ledger import (
    ClaimVersion,
    ContextVersion,
    MathState,
    save_state,
    state_path,
)
from argus.verticals.math.objective_mode import set_objective
from argus.verticals.math.stages import stage_completion_issues


def _project(tmp_path: Path) -> Path:
    """A math project that passes every gate other than the ledger."""
    set_objective(tmp_path, mode="targeted", goal="G")
    pipeline = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(pipeline.read_text(encoding="utf-8"))
    state["verification_profile"] = "develop"
    pipeline.write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PROOF_GRAPH.json").write_text(
        json.dumps({
            "goal": "G",
            "routes": [{"name": "route", "status": "current", "evidence": ""}],
            "nodes": {
                "G": {
                    "statement": "G",
                    "status": "proved",
                    "is_goal": True,
                    "depends_on": [],
                    "reviewer_confirmed": True,
                }
            },
        }),
        encoding="utf-8",
    )
    assert stage_completion_issues("solve", tmp_path) == ()
    return tmp_path


def _ledger(root: Path) -> MathState:
    """One context, one claim stated against it. Consistent by construction."""
    state = MathState()
    context = state.add_context(
        ContextVersion(context_id="ctx", version=1, statement="the problem")
    )
    state.add_claim(
        ClaimVersion(
            claim_id="C1",
            version=1,
            context=context.ref(),
            natural_statement="the theorem",
        )
    )
    save_state(root, state)
    return state


def test_a_project_with_no_ledger_pays_nothing(tmp_path: Path) -> None:
    """The rule the Lean sources already follow: absence costs nothing.

    Most missions in this runtime are not mathematical and will never write a
    ``MATH_STATE.json``. If keeping no ledger were a defect, every one of them
    would stop completing the moment this gate shipped.
    """
    root = _project(tmp_path)

    assert not state_path(root).exists()
    assert stage_completion_issues("solve", root) == ()
    assert stage_completion_issues("review", root) == ()


def test_a_consistent_ledger_does_not_block(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _ledger(root)

    assert state_path(root).exists()
    assert stage_completion_issues("solve", root) == ()


def test_a_claim_left_on_a_superseded_context_blocks_the_stage(
    tmp_path: Path,
) -> None:
    """The defect this gate exists for.

    Revising a context is how a project quietly starts proving a different
    theorem: the definitions move and the claims below keep their old wording.
    ``validate`` has always reported it; before this wiring nobody asked.
    """
    root = _project(tmp_path)
    state = _ledger(root)
    state.revise_context("ctx", definitions={"term": "a corrected meaning"})
    save_state(root, state)

    issues = stage_completion_issues("solve", root)

    assert issues, "a claim stranded on an old context finished the stage"
    assert any("superseded context" in issue for issue in issues), issues
    # The rendered shape, not just the fact of an issue: this reaches a reader
    # alongside Lean and literature findings, and all three have to read alike.
    assert any(issue.startswith("$.claims[") for issue in issues), issues


def test_an_unreadable_ledger_is_a_defect_rather_than_a_crash(
    tmp_path: Path,
) -> None:
    """A corrupt ledger must block, and must not take the gate down with it.

    ``load_state`` raises on a file it cannot parse, which is right: silently
    reading it as empty would discard every proof the project recorded. But an
    exception escaping here would abort the whole gate, and a gate that reports
    nothing is indistinguishable from a gate that found nothing wrong.
    """
    root = _project(tmp_path)
    _ledger(root)
    state_path(root).write_text("{not json", encoding="utf-8")

    issues = stage_completion_issues("solve", root)

    assert issues, "a ledger nobody can read finished the stage"
    assert any("MATH_STATE.json" in issue for issue in issues), issues


def test_the_blocking_message_names_a_remedy_that_exists(tmp_path: Path) -> None:
    """A gate that blocks has to say something the agent can actually do.

    The message used to offer two ways out, and one of them — "record why it
    still holds" — was implemented nowhere: there is no way to mark a claim as
    unaffected by a revision. That was harmless while this issue was advisory.
    Once it blocks a stage it stops being harmless, because a blocked agent
    will try what the message says and find nothing there.
    """
    root = _project(tmp_path)
    state = _ledger(root)
    state.revise_context("ctx", definitions={"term": "a corrected meaning"})
    save_state(root, state)

    issue = next(
        item for item in stage_completion_issues("solve", root) if "superseded" in item
    )

    assert "revise_claim" in issue, issue
    assert "record why it still holds" not in issue, issue

    # Not just named — doing what it says is what clears the block. An escape
    # hatch nobody verified is the thing this test exists to keep out.
    state.revise_claim("C1", context=state.latest_context("ctx").ref())
    save_state(root, state)

    assert stage_completion_issues("solve", root) == ()


def test_the_gate_does_not_judge_whether_the_mathematics_is_finished(
    tmp_path: Path,
) -> None:
    """The limit, pinned so a later change has to be deliberate.

    A claim with no evidence at all is ``proposed``, not ``closed_kernel`` — and
    that is not a structural defect. Whether an unproved claim may end a stage
    is a question about the requested bar, owned by the objective mode, and
    answering it here would make "the ledger is consistent" and "the theorem is
    proved" the same check.
    """
    root = _project(tmp_path)
    _ledger(root)

    assert stage_completion_issues("solve", root) == ()
