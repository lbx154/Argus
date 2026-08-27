"""modern_poetry-vertical stage definitions.

The THIRD literary vertical: modern free verse / prose poems (zh or en). It has NO
metrical machine layer — the only deterministic gate checks the declared hard
constraints. Imagery/lineation/tone/cliché are live-reviewer judgements.

Stages (``completion_gate="none"``):
1. **intake**: record ``poetry/task_envelope.json`` + derive ``poetry/poem_brief.json``.
2. **plan**: fix the ``poetry/form_spec.json`` (language, line count, banned words)
   and an imagery/tension plan.
3. **compose**: write ``poetry/draft_poem.txt``.
4. **form_check**: machine-check the declared hard constraints -> ``poetry/form_report.json``.
5. **review**: reviewer emits ``poetry/review.json`` (hard-constraint blocking + craft live).
6. **revise**: ``poetry/final_poem.txt`` + ``poetry/revision_plan.json`` + ``poetry/artifact_manifest.json``.
"""
from __future__ import annotations

import json
from pathlib import Path

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ["intake", "plan", "compose", "form_check", "review", "revise"]
CHECKLIST_OPTIONAL_STAGES = ("intake", "plan", "compose", "revise")
completion_gate = "none"

def stage_completion_issues(stage: str, project_root: Path) -> tuple[str, ...]:
    if stage in {"form_check", "revise"}:
        from .form import check_form

        poetry = project_root / "poetry"
        name = "draft_poem.txt" if stage == "form_check" else "final_poem.txt"
        try:
            poem = (poetry / name).read_text(encoding="utf-8")
            spec = json.loads((poetry / "form_spec.json").read_text(encoding="utf-8"))
            findings = check_form(poem, spec)
        except (OSError, ValueError) as exc:
            return (f"modern poetry inputs invalid: {exc}",)
        return tuple(finding["detail"] for finding in findings)
    return ()

CHECKLIST_STAGE_ORDER = tuple(STAGE_ORDER)

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "form_check": (
        ChecklistItem(
            id="hard-constraints-met",
            statement="The draft meets every DECLARED hard constraint (language, "
            "line count, banned words); no aesthetic claim is made here.",
            evidence_hint="poetry/form_report.json has no findings",
        ),
    ),
    "review": (
        ChecklistItem(
            id="craft-is-live",
            statement="Imagery/lineation/tone/cliché are NON-blocking live-reviewer "
            "judgements, never a mechanized or scored capability.",
            evidence_hint="review.json craft findings marked non-blocking",
        ),
    ),
}


def role_banner(role: str) -> str:
    common = (
        "MISSION TYPE: MODERN FREE VERSE (zh/en). The deliverable is a modern poem "
        "that meets its DECLARED hard constraints (language/line-count/banned words) "
        "and carries a real central image/tension. There is NO 平仄/韵 machine check "
        "— free verse is not classical. Craft is live-judged, never scored.\n"
    )
    if role == "planner":
        return common + ("Drive intake -> plan -> compose -> form_check -> review -> "
                         "revise. Fix the form_spec BEFORE composing.")
    if role == "engineer":
        return common + (
            "(1) Record poetry/task_envelope.json and derive the brief. (2) In plan "
            "fix poetry/form_spec.json (language, any line count, banned words) and an "
            "imagery/tension plan. (3) Compose. (4) Run form-check and fix EVERY "
            "declared-constraint violation. (5) Record poetry/source_usage.json "
            "(empty uses[] if none) and poetry/artifact_manifest.json. (6) Avoid "
            "meaningless line breaks and cliché imagery — but that is your judgement, "
            "not a machine gate.")
    if role == "reviewer":
        return common + (
            "You gate the poem. Hard-constraint findings are BLOCKING and mirror the "
            "machine form report. Imagery/lineation/tone/cliché/coherence are "
            "NON-BLOCKING live judgements — never a faked score. Follow the 'Modern "
            "Free-Verse Review' skill. Emit poetry/review.json per the shared "
            "literary review contract.")
    return common
