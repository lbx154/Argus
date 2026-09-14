"""Regression test: a targeted math project cannot complete with its goal open.

``math_objective_mode="targeted"`` means one named goal G: prove it or refute
it. Nothing in the deterministic completion path asked whether G was closed.

``stage_completion_issues`` read the objective mode only to decide whether
``research/PROOF_GRAPH.json` had to exist, then ran ``graph.validate()`` —
which is purely structural (status vocabulary, unknown ids, cycles,
proved-without-reviewer_confirmed). ``ProofGraph.gap()``, the one function that
answers "what does G still rest on", had exactly one caller in the tree: the
standalone operator CLI ``proof_graph_check``.

Downstream is no help. ``_review_certifies_completion`` checks reviewer status
plus ``research_completion_issue``, which is keyed on the research *strength*
axis (result_class / novelty / significance vs the target level) and discards
``vertical`` and ``mission_scope`` outright. The two persisted axes — objective
mode and research target level — were never intersected with each other, and
neither was intersected with the proof-graph gap.

So a targeted project whose root node was ``status="open"`` reached
``decision=complete``, ``complete_final_stage`` stamped a valid contract
fingerprint, and ``vertical_completion_certificate_status`` returned
``{"ok": true}`` — with the named goal still open on disk. Raising the research
target to ``doctoral`` did not close it: a genuine ``new_theorem`` about a side
lemma completes identically.

The only thing standing in the way was the reviewer skill prose, and the
reviewer is never told the persisted ``math_goal`` or the gap — and the failure
scenario is by construction one where its verdict is ``done``.

Citations:
- argus/verticals/math/stages.py — ``_targeted_goal_closure_issues``
- argus/verticals/math/proof_graph.py — ``ProofGraph.gap``
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.verticals.math.objective_mode import set_objective
from argus.verticals.math.stages import stage_completion_issues

GOAL = "G: every even integer n > 2 is a sum of two primes"
LEMMA = "L1: a counting bound for prime pairs"


def _project(tmp_path: Path, *, mode: str, nodes: dict, goal: str = GOAL) -> Path:
    (tmp_path / ".argus").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "math", "current_stage": "review"}) + "\n",
        encoding="utf-8",
    )
    set_objective(tmp_path, mode=mode, goal=goal if mode == "targeted" else "")
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PROOF_GRAPH.json").write_text(
        json.dumps({"goal": goal, "nodes": nodes}, indent=2) + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _open_goal() -> dict:
    return {GOAL: {"statement": GOAL, "is_goal": True, "status": "open"}}


def _closed_goal() -> dict:
    return {
        GOAL: {
            "statement": GOAL,
            "is_goal": True,
            "status": "proved",
            "reviewer_confirmed": True,
        }
    }


def test_an_open_goal_blocks_review(tmp_path: Path) -> None:
    root = _project(tmp_path, mode="targeted", nodes=_open_goal())

    issues = stage_completion_issues("review", root)

    assert any("still rests on" in issue for issue in issues), issues
    assert any(GOAL in issue for issue in issues), issues


def test_a_proved_goal_completes(tmp_path: Path) -> None:
    """The gate must not become a wall: a closed goal still passes."""
    root = _project(tmp_path, mode="targeted", nodes=_closed_goal())

    assert stage_completion_issues("review", root) == ()


def test_a_side_lemma_proved_is_not_the_goal_closed(tmp_path: Path) -> None:
    """The doctoral variant of the reproduction: real work, wrong question.

    A verified new theorem about ``L1`` is a genuine result, and every
    strength check in ``research_completion_issue`` passes on it. It is still
    not a proof of G, and a targeted project said it was done.
    """
    root = _project(
        tmp_path,
        mode="targeted",
        nodes={
            GOAL: {
                "statement": GOAL,
                "is_goal": True,
                "status": "open",
                "depends_on": [LEMMA],
            },
            LEMMA: {
                "statement": LEMMA,
                "status": "proved",
                "reviewer_confirmed": True,
            },
        },
    )

    issues = stage_completion_issues("review", root)

    assert any("still rests on" in issue for issue in issues), issues
    assert any(GOAL in issue for issue in issues), issues


def test_an_unmeasurable_gap_blocks_review(tmp_path: Path) -> None:
    """No goal node means the gap is unknown, which is not the same as zero."""
    root = _project(
        tmp_path,
        mode="targeted",
        nodes={LEMMA: {"statement": LEMMA, "status": "proved"}},
    )

    issues = stage_completion_issues("review", root)

    assert any("unmeasurable" in issue for issue in issues), issues


@pytest.mark.parametrize("stage", ["scope", "solve"])
def test_earlier_stages_are_not_blocked_by_an_open_goal(
    tmp_path: Path, stage: str
) -> None:
    """The whole job of ``solve`` is to shrink the gap.

    Blocking it on the gap being zero is bug #41's shape — a stage that can
    never be closed — so the check is deliberately gated to ``review``.
    """
    root = _project(tmp_path, mode="targeted", nodes=_open_goal())

    issues = stage_completion_issues(stage, root)

    assert not any("still rests on" in issue for issue in issues), issues


def test_exploratory_mode_is_untouched(tmp_path: Path) -> None:
    """Exploratory has no single G — demanding one rejects good work."""
    root = _project(tmp_path, mode="exploratory", nodes=_open_goal())

    issues = stage_completion_issues("review", root)

    assert not any("still rests on" in issue for issue in issues), issues
