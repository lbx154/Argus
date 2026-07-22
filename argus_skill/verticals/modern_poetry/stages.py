"""modern_poetry-vertical stage definitions.

The THIRD literary vertical: modern free verse / prose poems (zh or en), consuming
the same four shared contracts as fiction and classical_poetry. It has NO metrical
machine layer — the only deterministic gate is ``form-check`` (declared hard
constraints). Imagery/lineation/tone/cliché are live-reviewer judgements.

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

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ["intake", "plan", "compose", "form_check", "review", "revise"]
CHECKLIST_OPTIONAL_STAGES = ("intake", "plan", "compose", "revise")
completion_gate = "none"

_PIPELINE_CHECK = ("Pipeline state present", "test -f research/PIPELINE_STATE.json")
_CHECKS = "{python} -m argus_skill.verticals.modern_poetry.checks"

STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "intake": [
        _PIPELINE_CHECK,
        ("Task envelope recorded", "test -s poetry/task_envelope.json"),
        ("Task envelope valid and modern-poetry-consumable",
         f"{_CHECKS} intake-validate poetry/task_envelope.json"),
        ("Poem brief produced", "test -s poetry/poem_brief.json"),
        ("Source registry is well-formed", f"{_CHECKS} source-registry"),
    ],
    "plan": [
        _PIPELINE_CHECK,
        ("Form spec + imagery plan produced", "test -s poetry/form_spec.json"),
    ],
    "compose": [
        _PIPELINE_CHECK,
        ("Draft poem written", "test -s poetry/draft_poem.txt"),
    ],
    "form_check": [
        _PIPELINE_CHECK,
        ("Form report produced", "test -s poetry/form_report.json"),
        ("Poem meets declared hard constraints (language/line-count/banned-words)",
         f"{_CHECKS} form-check poetry/draft_poem.txt poetry/form_spec.json"),
    ],
    "review": [
        _PIPELINE_CHECK,
        ("Structured review produced", "test -s poetry/review.json"),
        ("Review conforms to the literary review contract",
         f"{_CHECKS} review-validate poetry/review.json"),
        ("Source-usage ledger produced (explicit, empty uses[] if none consulted)",
         "test -s poetry/source_usage.json"),
        ("Every recorded source use is rights-defensible",
         f"{_CHECKS} check-usage poetry/source_usage.json"),
    ],
    "revise": [
        _PIPELINE_CHECK,
        ("Final poem and revision plan produced",
         "test -s poetry/final_poem.txt && test -s poetry/revision_plan.json"),
        ("Revision plan covers every blocking finding",
         f"{_CHECKS} check-plan poetry/review.json poetry/revision_plan.json"),
        ("Artifact manifest records the chain", "test -s poetry/artifact_manifest.json"),
        ("Artifact manifest conforms to the shared lineage contract",
         f"{_CHECKS} manifest-validate poetry/artifact_manifest.json"),
        ("Every artifact the manifest records is present",
         f"{_CHECKS} manifest-content poetry/artifact_manifest.json"),
    ],
}

_REVIEW_SKILL = "reviewer/modern-verse-review.md"

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "intake": (_REVIEW_SKILL,
               "Confirm the brief derives from poetry/task_envelope.json and the "
               "form_spec captures the declared hard constraints (language, any "
               "line count, banned words).",
               ["poetry/task_envelope.json", "poetry/poem_brief.json"]),
    "plan": (_REVIEW_SKILL,
             "Gate the plan: form_spec fixed; an imagery/tension plan with a "
             "central image or a turn, not a list of pretty lines.",
             ["poetry/form_spec.json", "poetry/poem_brief.json"]),
    "compose": (_REVIEW_SKILL,
                "First read against the brief. Craft notes non-blocking here.",
                ["poetry/draft_poem.txt", "poetry/poem_brief.json"]),
    "form_check": (_REVIEW_SKILL,
                   "Confirm the machine form report: declared language/line-count/"
                   "banned-word constraints all hold. This is the ONLY machine gate; "
                   "do not treat it as a quality judgement.",
                   ["poetry/form_report.json", "poetry/draft_poem.txt"]),
    "review": (_REVIEW_SKILL,
               "Emit typed findings. Hard-constraint findings (language/line_count/"
               "banned_word/empty_line) are BLOCKING and mirror the machine report. "
               "CRAFT (imagery/lineation/tone/cliche/coherence/reference_fidelity) "
               "are NON-BLOCKING live judgements — never a faked numeric score.",
               ["poetry/draft_poem.txt", "poetry/form_report.json"]),
    "revise": (_REVIEW_SKILL,
               "Verify every BLOCKING constraint is met (final re-passes form-check), "
               "revision_plan.json derives from review.json, and artifact_manifest.json "
               "records the chain (final supersedes draft, traces to draft + review).",
               ["poetry/final_poem.txt", "poetry/review.json",
                "poetry/revision_plan.json", "poetry/artifact_manifest.json"]),
}

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
