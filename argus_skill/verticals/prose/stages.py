"""prose-vertical stage definitions.

The FOURTH literary vertical: literary/narrative prose (抒情/叙事散文/随笔/回忆, zh or
en), consuming the same four shared contracts. Machine layer is honestly thin
(prose_state structure + declared hard constraints); the craft — concrete
observation, keeping fact distinct from memory, real paragraph movement, an earned
ending — is live-reviewer.

Stages (``completion_gate="none"``):
1. **intake**: record ``prose/task_envelope.json`` + derive ``prose/prose_brief.json``.
2. **plan**: declare ``prose/prose_state.json`` (narrative_center / observation /
   factual_anchors / memory_boundary / paragraph_movement / ending_strategy).
3. **draft**: write ``prose/draft.md``.
4. **structure_check**: machine-check prose_state completeness + hard constraints
   -> ``prose/structure_report.json``.
5. **review**: reviewer emits ``prose/review.json`` (structure blocking + craft live).
6. **revise**: ``prose/final.md`` + ``prose/revision_plan.json`` + ``prose/artifact_manifest.json``.
"""
from __future__ import annotations

import json
from pathlib import Path

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ["intake", "plan", "draft", "structure_check", "review", "revise"]
CHECKLIST_OPTIONAL_STAGES = ("intake", "plan", "draft", "revise")
completion_gate = "none"

def stage_completion_issues(stage: str, project_root: Path) -> tuple[str, ...]:
    if stage in {"structure_check", "revise"}:
        from .structure import check_draft, validate_prose_state

        prose = project_root / "prose"
        name = "draft.md" if stage == "structure_check" else "final.md"
        try:
            draft = (prose / name).read_text(encoding="utf-8")
            state = json.loads((prose / "prose_state.json").read_text(encoding="utf-8"))
            findings = validate_prose_state(state)
            findings += check_draft(draft, state.get("spec"))
        except (OSError, ValueError) as exc:
            return (f"prose structure inputs invalid: {exc}",)
        return tuple(finding["detail"] for finding in findings)
    return ()

CHECKLIST_STAGE_ORDER = tuple(STAGE_ORDER)

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "structure_check": (
        ChecklistItem(
            id="prose-state-complete",
            statement="prose_state declares narrative_center/observation/"
            "factual_anchors/memory_boundary/paragraph_movement/ending_strategy.",
            evidence_hint="prose/structure_report.json has no structure findings",
        ),
    ),
    "review": (
        ChecklistItem(
            id="fact-memory-is-live",
            statement="fact/memory boundary and fabrication are NON-blocking "
            "live-reviewer judgements, never mechanized; an invented fact is never "
            "silently passed as the operator's memory.",
            evidence_hint="review.json fact_memory/fabrication findings are live",
        ),
    ),
}


def role_banner(role: str) -> str:
    common = (
        "MISSION TYPE: LITERARY PROSE (散文/随笔/回忆, zh/en). The deliverable is a "
        "prose piece with concrete observation and a clear boundary between fact and "
        "memory. There is NO meter machine check; structure completeness and declared "
        "hard constraints are the only machine gate. Craft is live-judged.\n"
    )
    if role == "planner":
        return common + ("Drive intake -> plan -> draft -> structure_check -> review "
                         "-> revise. Declare prose_state BEFORE drafting.")
    if role == "engineer":
        return common + (
            "(1) Record prose/task_envelope.json and derive the brief. (2) In plan "
            "declare prose/prose_state.json (narrative_center/observation_subject/"
            "factual_anchors/memory_boundary/paragraph_movement/ending_strategy). "
            "(3) Draft. NEVER invent a fact the operator did not give — keep memory "
            "and fact distinct per the declared boundary. (4) Run structure-check and "
            "fix every structural/constraint violation. (5) Record prose/"
            "source_usage.json (empty uses[] if none) and prose/artifact_manifest.json. "
            "(6) Avoid slogan endings and template philosophizing — your judgement.")
    if role == "reviewer":
        return common + (
            "You gate the prose. Structure + hard-constraint findings are BLOCKING and "
            "mirror the machine report. observation/fact_memory/fabrication/movement/"
            "ending/template are NON-BLOCKING live judgements — flag any invented fact "
            "or crossed memory_boundary. Follow the 'Prose Review' skill. Emit "
            "prose/review.json per the shared literary review contract.")
    return common
