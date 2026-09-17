"""The claim is fixed at Idea selection; a weak result optimizes the code.

Two failure shapes motivated this policy. The task handed to the implementer
was a few sentences that defined the method inside the notes and shrank the
dataset list on re-issue, so the code implemented something other than the
claimed method. Then the first negative result was treated as a finding and
written up as a restricted-case paper instead of being iterated on. These
tests pin the playbooks, the prompt fragments, the checklist and the two
handoff skills that make the claim immovable by any role but the operator,
turn a negative result into a diagnosis ladder, and give the Engineer a
complete brief and the Reviewer a packet built from code anchors.
"""
from __future__ import annotations

from pathlib import Path

import argus
from argus.verticals.research.library_preparation import STAGE_PLAYBOOK_PATHS
from argus.verticals.research.prompt_policy import render_role_prompt_fragment
from argus.verticals.research.stages import STAGE_CHECKLISTS, role_banner

SKILLS = Path(argus.__file__).parent / "verticals" / "research" / "skills"
ENGINEER = SKILLS / "engineer"
REVIEWER = SKILLS / "reviewer"

LADDER = (
    "implementation fidelity",
    "positive control",
    "one factor at a time",
    "baseline fairness",
    "three distinct",
)


def _fragment(role: str, stage: str, operation: str = "execute") -> str:
    return render_role_prompt_fragment(
        role=role, operation=operation, stage=stage, scope="", project_root=None,
    )


def _text(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_experiment_playbook_replaces_thesis_rederivation_with_a_diagnosis_ladder() -> None:
    text = _text(SKILLS / STAGE_PLAYBOOK_PATHS["experiment"])
    lowered = text.lower()

    assert "### Fixed claim: iterate until it works" in text
    for phrase in LADDER:
        assert phrase in lowered, phrase
    assert "only the operator may change it" in lowered
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
    assert "engineer/implementation-brief.md" in text
    assert "engineer/write-for-review.md" in text
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
    assert "only the operator edits the claim" in grind
    assert "three distinct, diagnosed attempts" in grind
    claim = _text(ENGINEER / "result-to-claim.md").lower()
    assert "fixed at idea selection" in claim
    assert "restate the claim to match what the code did" in claim
    setup = _text(ENGINEER / "suspect-the-setup.md").lower()
    assert "rewording the claim to match the number" in setup
    contract = _text(ENGINEER / "hypothesis-implementation-contract.md").lower()
    assert "never resolve either by rewording the card" in contract


def test_reviewer_skill_treats_claim_drift_and_early_negatives_as_repairs() -> None:
    review = _text(REVIEWER / "experiment-results-review.md").lower()

    assert "review packet" in review
    assert "never accept claim drift" in review
    assert "fewer than three distinct, diagnosed attempts" in review
    assert "is a repair request" in review
    assert "# @component" in review
    assert "re-deriving the thesis" not in review


def test_planner_fragment_never_rewrites_the_claim_and_hands_over_a_brief() -> None:
    text = _fragment("planner", "experiment", operation="plan")

    assert "never rewrites the claim" in text
    assert "never schedules a negative-result or restricted-case paper" in text
    assert "next undiagnosed rung" in text
    assert "operator question with the evidence" in text
    assert "engineer/implementation-brief.md" in text
    assert "acceptance is executable checks, not adjectives" in text
    assert "one task is one brief" in text
    assert "stay verbatim" in text
    assert "re-derivation" not in text
    assert "refuted closes its family" not in text


def test_reviewer_fragment_reads_the_packet_first_and_refuses_claim_drift() -> None:
    text = _fragment("reviewer", "experiment", operation="evaluate")

    assert "review packet" in text
    assert text.index("review packet") < text.index("METHOD.md")
    assert "claim drift" in text
    assert "without a '# @component' anchor" in text
    assert "fewer than three diagnosed attempts, is a repair request" in text


def test_engineer_fragment_points_to_write_for_review_anchors() -> None:
    text = _fragment("engineer", "experiment")

    assert "engineer/write-for-review.md" in text
    assert "'# @component <name>'" in text
    assert "builds the Reviewer's packet from these anchors" in text
    revision = _fragment("engineer", "review")
    assert "Do not default to weaker claims" in revision
    assert "never rewrite the claim to match the code" in revision
    assert "adjust the interpretation honestly" not in revision


def test_paper_policy_leads_with_the_strongest_result_and_writes_nothing_defensively() -> None:
    fragment = _fragment("engineer", "paper")
    playbook = _text(SKILLS / STAGE_PLAYBOOK_PATHS["paper"]).lower()
    paper_items = " ".join(item.statement for item in STAGE_CHECKLISTS["paper"]).lower()

    assert "no defensive writing" in fragment
    assert "written only when that claim is supported" in fragment
    assert "one honest paragraph" in fragment
    assert "legitimate paper when its evidence" not in fragment
    assert "no defensive writing" in playbook
    assert "led by the strongest supported result" in playbook
    assert "one honest paragraph" in playbook
    assert "unless the operator has changed the claim" in playbook
    assert "no defensive writing" in paper_items
    assert "negative or boundary thesis" not in paper_items


def test_checklist_and_banners_fix_the_claim_and_hold_narrowed_acceptances() -> None:
    experiment = " ".join(item.statement for item in STAGE_CHECKLISTS["experiment"])
    lowered = experiment.lower()

    assert "fixed at idea selection" in lowered
    assert "diagnosis ladder" in lowered
    for phrase in ("implementation fidelity", "baseline fairness", "three distinct"):
        assert phrase in lowered, phrase
    assert "never a narrowed claim" in lowered
    assert "re-derive" not in lowered
    assert "negative or boundary thesis" not in lowered
    assert "narrower than METHOD.md states is not stage completion" in role_banner("manager")
    assert "next rung of the diagnosis ladder" in role_banner("manager")
    assert "make the code satisfy it, never the card fit the code" in role_banner("engineer")
    assert "fixed until the operator changes it" in role_banner("planner")


def test_handoff_skills_exist_with_their_headings_and_stay_short() -> None:
    brief = _text(ENGINEER / "implementation-brief.md")
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

    review = _text(ENGINEER / "write-for-review.md")
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
    assert "invisible to the Reviewer" in review

    def body_words(text: str) -> int:
        _front, _, rest = text.split("---", 2)
        return len(rest.split())

    assert body_words(brief) <= 350
    # 300 -> 380 for the claim-attainment statement (one entry per clause,
    # host-resolved pointer); trim before raising again.
    assert body_words(review) <= 380
