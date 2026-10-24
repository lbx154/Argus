"""A weak result optimizes the code; only the claim's owner changes the claim.

Two failure shapes motivated this policy. The task handed to the implementer
was a few sentences that defined the method inside the notes and shrank the
dataset list on re-issue, so the code implemented something other than the
claimed method. Then the first negative result was treated as a finding and
written up as a restricted-case paper instead of being iterated on. These
tests pin the playbooks, the prompt fragments, the checklist and the two
handoff skills that keep the claim out of reach of whoever implements it,
turn a negative result into a diagnosis ladder, and give the Engineer a
complete brief and the Reviewer a packet built from code anchors.

Ownership follows who chose the idea. Project s-a43d1018 asked for "a general
method"; Argus chose the route itself and then treated its pick as
operator-owned, so 40 hours of repairs could only patch inside it. An
operator-locked claim still changes only through the operator; an
Argus-selected working hypothesis may be revised in one reviewed task that
keeps earlier results and never reuses selection data as confirmation.
"""
from __future__ import annotations

from pathlib import Path

import argus
from argus.verticals.research.library_preparation import STAGE_PLAYBOOK_PATHS
from argus.verticals.research.stages import STAGE_CHECKLISTS, role_banner

SKILLS = Path(argus.__file__).parent / "verticals" / "research" / "skills"
ENGINEER = SKILLS / "engineer"
REVIEWER = SKILLS / "reviewer"

LADDER = (
    "implementation fidelity",
    "positive control",
    "one factor at a time",
    "baseline fairness",
    "named the rung it was testing",
)


def _text(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_experiment_playbook_replaces_thesis_rederivation_with_a_diagnosis_ladder() -> None:
    text = _text(SKILLS / STAGE_PLAYBOOK_PATHS["experiment"])
    lowered = text.lower()

    assert "### The claim: iterate, then revise or report" in text
    for phrase in LADDER:
        assert phrase in lowered, phrase
    assert "only the operator changes the claim" in lowered
    assert "working hypothesis" in lowered
    assert "development data, never as independent confirmation" in lowered
    assert "idea selection is not reopened" in lowered
    assert "not when a count of tries is reached" in lowered
    assert "operator_options" in text
    assert "never a narrowed claim" in lowered
    assert '"restricted case"' in text and '"negative result"' in text
    assert "re-derive the thesis" not in lowered
    assert "re-derivation" not in lowered
    # The ladder is worked in order, one rung per attempt, with evidence.
    order = [lowered.index(phrase) for phrase in (
        "implementation fidelity", "setup and evaluator", "hyperparameters and recipe",
        "scale and data", "baseline fairness", "method variants that still satisfy",
    )]
    assert order == sorted(order)
    # The brief and the review anchors are part of the implementation step.
    assert "engineer/method-card.md" in text
    assert "# @component <name>" in text


def test_no_research_skill_re_derives_the_thesis_or_scopes_the_claim_to_the_code() -> None:
    paths = [
        *SKILLS.glob("*.md"),
        *ENGINEER.glob("*.md"),
        *REVIEWER.glob("*.md"),
    ]
    for path in paths:
        lowered = _text(path).lower()
        assert "re-derive the thesis" not in lowered, path.name
        assert "re-deriving the thesis" not in lowered, path.name
        assert "write the paper about *that*" not in lowered, path.name
    grind = _text(ENGINEER / "research-grind.md").lower()
    assert "the claim does not" in grind
    assert "the engineer never edits the claim on its own" in grind
    assert "named the rung it was testing" in grind
    assert "the claim's owner" in grind
    assert "restate the claim to match what the code did" in grind
    assert "rewording the claim to match the number" in grind
    card = _text(ENGINEER / "method-card.md").lower()
    assert "never resolve either by rewording the card" in card


def test_reviewer_skill_treats_claim_drift_and_early_negatives_as_repairs() -> None:
    review = _text(REVIEWER / "experiment-results-review.md").lower()

    assert "review packet" in review
    assert "never accept claim drift" in review
    assert "the number of attempts is not the test" in review
    assert "is a repair request" in review
    assert "# @component" in review
    assert "re-deriving the thesis" not in review


def test_checklist_and_banners_fix_the_claim_and_hold_narrowed_acceptances() -> None:
    experiment = " ".join(item.statement for item in STAGE_CHECKLISTS["experiment"])
    lowered = experiment.lower()

    assert "operator-locked idea" in lowered and "working hypothesis" in lowered
    assert "diagnosis ladder" in lowered
    for phrase in ("implementation fidelity", "baseline fairness", "the number of attempts is not the test"):
        assert phrase in lowered, phrase
    assert "three distinct" not in lowered
    assert "never present a narrowed claim" in lowered
    assert "keep the selected idea and current stage" in lowered
    assert "re-derive" not in lowered
    assert "negative or boundary thesis" not in lowered
    assert "narrower than METHOD.md states is not stage completion" in role_banner("manager")
    assert "next rung of the diagnosis ladder" in role_banner("manager")
    assert "never the card fit the code" in role_banner("engineer")
    assert "changes only as the experiment.repair item allows" in role_banner("planner")


def test_method_card_keeps_the_brief_headings_and_review_anchors() -> None:
    brief = _text(ENGINEER / "method-card.md")
    for heading in (
        "## Claim",
        "## Components to implement this task",
        "## Interfaces",
        "## Tests that must pass",
        "## Data and scale",
        "## Commands",
        "## Environment prerequisites",
        "## Definition of done",
        "## Out of scope",
    ):
        assert heading in brief, heading
    assert "verbatim" in brief
    assert "path/to/file.py:Symbol" in brief
    assert '"e.g." is not a dataset list' in brief
    assert "No adjectives" in brief

    review = brief
    for heading in ("## Anchors", "## Configuration", "## Shape of the code"):
        assert heading in review, heading
    for anchor in (
        "`# @component <name>`",
        "`# @simplified <name>: <why>`",
        "`# @reuses <library> <symbol>`",
        "`# why: <reason>`",
    ):
        assert anchor in review, anchor
    assert "imported" in review and "never copied" in review
    assert "review packet" in review
    assert "cannot locate cannot be credited" in review
