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

from ...skills.stage_checklists import ChecklistItem

STAGE_ORDER = ["intake", "plan", "draft", "structure_check", "review", "revise"]
completion_gate = "none"

_PIPELINE_CHECK = ("Pipeline state present", "test -f research/PIPELINE_STATE.json")
_CHECKS = "{python} -m argus_skill.verticals.prose.checks"

STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "intake": [
        _PIPELINE_CHECK,
        ("Task envelope recorded", "test -s prose/task_envelope.json"),
        ("Task envelope valid and prose-consumable",
         f"{_CHECKS} intake-validate prose/task_envelope.json"),
        ("Prose brief produced", "test -s prose/prose_brief.json"),
        ("Source registry is well-formed", f"{_CHECKS} source-registry"),
    ],
    "plan": [
        _PIPELINE_CHECK,
        ("Prose state declared", "test -s prose/prose_state.json"),
    ],
    "draft": [
        _PIPELINE_CHECK,
        ("Draft written", "test -s prose/draft.md"),
    ],
    "structure_check": [
        _PIPELINE_CHECK,
        ("Structure report produced", "test -s prose/structure_report.json"),
        ("prose_state complete and draft meets declared hard constraints",
         f"{_CHECKS} structure-check prose/draft.md prose/prose_state.json"),
    ],
    "review": [
        _PIPELINE_CHECK,
        ("Structured review produced", "test -s prose/review.json"),
        ("Review conforms to the literary review contract",
         f"{_CHECKS} review-validate prose/review.json"),
        ("Source-usage ledger produced (explicit, empty uses[] if none consulted)",
         "test -s prose/source_usage.json"),
        ("Every recorded source use is rights-defensible",
         f"{_CHECKS} check-usage prose/source_usage.json"),
    ],
    "revise": [
        _PIPELINE_CHECK,
        ("Final prose and revision plan produced",
         "test -s prose/final.md && test -s prose/revision_plan.json"),
        ("Revision plan covers every blocking finding",
         f"{_CHECKS} check-plan prose/review.json prose/revision_plan.json"),
        ("Artifact manifest records the chain", "test -s prose/artifact_manifest.json"),
        ("Artifact manifest conforms to the shared lineage contract",
         f"{_CHECKS} manifest-validate prose/artifact_manifest.json"),
        ("Every artifact the manifest records is present",
         f"{_CHECKS} manifest-content prose/artifact_manifest.json"),
    ],
}

_REVIEW_SKILL = "reviewer/prose-review.md"

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "intake": (_REVIEW_SKILL,
               "Confirm the brief derives from prose/task_envelope.json (form is a "
               "prose form, language declared).",
               ["prose/task_envelope.json", "prose/prose_brief.json"]),
    "plan": (_REVIEW_SKILL,
             "Gate prose_state: narrative_center, observation_subject, "
             "factual_anchors, memory_boundary, paragraph_movement, ending_strategy "
             "all declared — the writer states what is observed and where fact ends.",
             ["prose/prose_state.json", "prose/prose_brief.json"]),
    "draft": (_REVIEW_SKILL,
              "First read against the brief + prose_state. Craft notes non-blocking.",
              ["prose/draft.md", "prose/prose_state.json"]),
    "structure_check": (_REVIEW_SKILL,
                        "Confirm the machine structure report: prose_state complete, "
                        "declared hard constraints met. This is not a quality gate.",
                        ["prose/structure_report.json", "prose/draft.md"]),
    "review": (_REVIEW_SKILL,
               "Emit typed findings. Structure + hard-constraint findings are "
               "BLOCKING and mirror the machine report. CRAFT (observation/"
               "fact_memory/fabrication/movement/imagery/ending/template) are "
               "NON-BLOCKING live judgements — most important: did the model invent "
               "a fact or cross the declared memory_boundary? Never fake a score.",
               ["prose/draft.md", "prose/prose_state.json", "prose/structure_report.json"]),
    "revise": (_REVIEW_SKILL,
               "Verify every BLOCKING structure/constraint finding is fixed (final "
               "re-passes structure-check), revision_plan.json derives from "
               "review.json, and artifact_manifest.json records the chain (final "
               "supersedes draft, traces to draft + review).",
               ["prose/final.md", "prose/review.json", "prose/revision_plan.json",
                "prose/artifact_manifest.json"]),
}

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
