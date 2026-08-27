"""literary_editor-vertical stage definitions.

The FIFTH literary consumer — an EDITING service over existing text, reusing the
framework Reviewer + revise capability. It consumes the same four shared contracts
as the genre verticals. Its machine layer checks non-empty output and explicit
must-keep constraints; edit quality and semantic scope are live Reviewer judgments.

Stages (``completion_gate="none"``):
1. **intake**: record ``editor/task_envelope.json`` + ``editor/source.txt`` +
   derive ``editor/edit_brief.json`` (mode, goal, must_keep, allow_new_facts).
2. **diagnose**: reviewer emits ``editor/review.json`` — findings on the SOURCE
   against the goal (edit-discipline blocking + craft live).
3. **revision_plan**: ``editor/revision_plan.json`` derived from the review.
4. **edit**: produce ``editor/edited.txt`` honoring the mode + must_keep; stage
   completion checks the deterministic edit constraints.
5. **verify**: produce ``editor/change_summary.json`` and
   ``editor/artifact_manifest.json``.
"""
from __future__ import annotations

import json
from pathlib import Path

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ["intake", "diagnose", "revision_plan", "edit", "verify"]
CHECKLIST_OPTIONAL_STAGES = ("intake", "diagnose", "revision_plan")
completion_gate = "none"

def stage_completion_issues(stage: str, project_root: Path) -> tuple[str, ...]:
    if stage != "edit":
        return ()

    from .edit_ops import check_edit

    editor = project_root / "editor"
    try:
        source = (editor / "source.txt").read_text(encoding="utf-8")
        edited = (editor / "edited.txt").read_text(encoding="utf-8")
        brief = json.loads((editor / "edit_brief.json").read_text(encoding="utf-8"))
        if not isinstance(brief, dict) or "mode" not in brief:
            raise ValueError("edit_brief must be an object carrying a 'mode'")
        findings = check_edit(source, edited, brief["mode"], brief.get("must_keep"))
    except (OSError, ValueError) as exc:
        return (f"literary editor inputs invalid: {exc}",)
    return tuple(finding["detail"] for finding in findings)

CHECKLIST_STAGE_ORDER = tuple(STAGE_ORDER)

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "edit": (
        ChecklistItem(
            id="mode-discipline-held",
            statement="The Reviewer judged the semantic change against the stated "
            "mode and goal; every explicit must-keep segment survives verbatim.",
            evidence_hint="review judgment + editor edit-check",
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
        "capability — do NOT invent a new agent. Whether the edit is good or stayed "
        "within its semantic mandate is a live judgment; only non-empty output and "
        "explicit must-keep constraints are machine checked.\n"
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
            "You gate the edit. Only empty output and dropped explicit must-keep "
            "segments mirror the machine check. Judge from the brief and semantic "
            "change whether the edit exceeded its mandate; character similarity and "
            "length are not verdicts. Flag any invented fact. Follow the "
            "'Literary Editing Review' skill. Emit editor/review.json per the shared "
            "literary review contract.")
    return common
