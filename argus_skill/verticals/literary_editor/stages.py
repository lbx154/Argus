"""literary_editor-vertical stage definitions.

The FIFTH literary consumer — an EDITING service over existing text, reusing the
framework Reviewer + revise capability. It consumes the same four shared contracts
as the genre verticals. Its machine layer is EDIT DISCIPLINE (genuinely
deterministic); whether the edit is GOOD is live-reviewer.

Stages (``completion_gate="none"``):
1. **intake**: record ``editor/task_envelope.json`` + ``editor/source.txt`` +
   derive ``editor/edit_brief.json`` (mode, goal, must_keep, allow_new_facts).
2. **diagnose**: reviewer emits ``editor/review.json`` — findings on the SOURCE
   against the goal (edit-discipline blocking + craft live).
3. **revision_plan**: ``editor/revision_plan.json`` derived from the review.
4. **edit**: produce ``editor/edited.txt`` honoring the mode + must_keep.
5. **verify**: machine edit-check + ``editor/change_summary.json`` +
   ``editor/artifact_manifest.json``.
"""
from __future__ import annotations

from ...skills.stage_checklists import ChecklistItem

STAGE_ORDER = ["intake", "diagnose", "revision_plan", "edit", "verify"]
completion_gate = "none"

_PIPELINE_CHECK = ("Pipeline state present", "test -f research/PIPELINE_STATE.json")
_CHECKS = "{python} -m argus_skill.verticals.literary_editor.checks"

STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "intake": [
        _PIPELINE_CHECK,
        ("Task envelope recorded", "test -s editor/task_envelope.json"),
        ("Task envelope valid and editor-consumable",
         f"{_CHECKS} intake-validate editor/task_envelope.json"),
        ("Source text recorded", "test -s editor/source.txt"),
        ("Edit brief produced", "test -s editor/edit_brief.json"),
        ("Source registry is well-formed", f"{_CHECKS} source-registry"),
    ],
    "diagnose": [
        _PIPELINE_CHECK,
        ("Diagnosis review produced", "test -s editor/review.json"),
        ("Review conforms to the literary review contract",
         f"{_CHECKS} review-validate editor/review.json"),
    ],
    "revision_plan": [
        _PIPELINE_CHECK,
        ("Revision plan produced", "test -s editor/revision_plan.json"),
        ("Revision plan covers every blocking finding",
         f"{_CHECKS} check-plan editor/review.json editor/revision_plan.json"),
    ],
    "edit": [
        _PIPELINE_CHECK,
        ("Edited text produced", "test -s editor/edited.txt"),
        ("Edit respects its mode discipline + must-keep list",
         f"{_CHECKS} edit-check editor/source.txt editor/edited.txt "
         "editor/edit_brief.json"),
    ],
    "verify": [
        _PIPELINE_CHECK,
        ("Change summary produced", "test -s editor/change_summary.json"),
        ("Source-usage ledger produced (explicit, empty uses[] if none)",
         "test -s editor/source_usage.json"),
        ("Every recorded source use is rights-defensible",
         f"{_CHECKS} check-usage editor/source_usage.json"),
        ("Artifact manifest records the chain", "test -s editor/artifact_manifest.json"),
        ("Artifact manifest conforms to the shared lineage contract",
         f"{_CHECKS} manifest-validate editor/artifact_manifest.json"),
        ("Every artifact the manifest records is present",
         f"{_CHECKS} manifest-content editor/artifact_manifest.json"),
    ],
}

_REVIEW_SKILL = "reviewer/edit-review.md"

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "intake": (_REVIEW_SKILL,
               "Confirm the brief derives from editor/task_envelope.json: an "
               "editing mode, a source text present, and the must_keep list "
               "captured. An editing mode without a source must have failed here.",
               ["editor/task_envelope.json", "editor/edit_brief.json",
                "editor/source.txt"]),
    "diagnose": (_REVIEW_SKILL,
                 "Produce findings on the SOURCE against the goal. Mark what MUST "
                 "be preserved (must_not_break). Do not rewrite here — diagnose.",
                 ["editor/source.txt", "editor/edit_brief.json"]),
    "revision_plan": (_REVIEW_SKILL,
                      "Gate the plan: it covers every blocking finding and carries "
                      "the must_not_break invariants into the edit instructions.",
                      ["editor/review.json", "editor/revision_plan.json"]),
    "edit": (_REVIEW_SKILL,
             "Verify EDIT DISCIPLINE: a critique did not rewrite; a proofread did "
             "not become a rewrite; an expand added material; every must-keep "
             "segment survives. Then judge (live, non-blocking) whether the edit "
             "reads better and — critically — whether any FACT was invented.",
             ["editor/source.txt", "editor/edited.txt", "editor/edit_brief.json"]),
    "verify": (_REVIEW_SKILL,
               "Confirm change_summary.json explains the edits, artifact_manifest "
               "records the chain (edited supersedes source, traces to source + "
               "review), and no invented fact slipped through.",
               ["editor/edited.txt", "editor/change_summary.json",
                "editor/artifact_manifest.json"]),
}

CHECKLIST_STAGE_ORDER = tuple(STAGE_ORDER)

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "edit": (
        ChecklistItem(
            id="mode-discipline-held",
            statement="The edit respected its mode: critique did not rewrite, "
            "proofread stayed close to the source, expand added material; every "
            "must-keep segment survives verbatim.",
            evidence_hint="editor edit-check passes (no blocking findings)",
        ),
    ),
    "verify": (
        ChecklistItem(
            id="no-invented-fact",
            statement="No fact was invented in a polish/proofread; fact fidelity "
            "and edit quality are recorded as NON-blocking live judgements.",
            evidence_hint="review.json fact_fidelity finding is live, not faked",
        ),
    ),
}


def role_banner(role: str) -> str:
    common = (
        "MISSION TYPE: LITERARY EDITING. The deliverable is an EDITED version of an "
        "existing text that respects its editing mode (rewrite/expand/polish/"
        "proofread/critique) and a must-keep list. Reuse the Reviewer + revise "
        "capability — do NOT invent a new agent. Whether the edit is GOOD is a live "
        "judgement; edit DISCIPLINE is the machine gate.\n"
    )
    if role == "planner":
        return common + ("Drive intake -> diagnose -> revision_plan -> edit -> "
                         "verify. The mode fixes what kind of edit is allowed.")
    if role == "engineer":
        return common + (
            "(1) Record editor/task_envelope.json + editor/source.txt and derive "
            "editor/edit_brief.json (mode, goal, must_keep). (2) Diagnose the source "
            "into editor/review.json — do NOT rewrite yet. (3) Derive "
            "editor/revision_plan.json. (4) Produce editor/edited.txt honoring the "
            "mode: a critique edits NOTHING; a proofread only fixes errors; an expand "
            "adds; NEVER drop a must-keep segment and NEVER invent a fact in a "
            "polish. (5) Record editor/change_summary.json, editor/source_usage.json "
            "(empty uses[] if none) and editor/artifact_manifest.json.")
    if role == "reviewer":
        return common + (
            "You gate the edit. Edit-discipline findings (mode_discipline/over_edit/"
            "no_expansion/must_not_break/empty) are BLOCKING and mirror the machine "
            "edit-check. edit_quality/fact_fidelity/coherence/over_reach are "
            "NON-BLOCKING live judgements — flag any invented fact. Follow the "
            "'Literary Editing Review' skill. Emit editor/review.json per the shared "
            "literary review contract.")
    return common
