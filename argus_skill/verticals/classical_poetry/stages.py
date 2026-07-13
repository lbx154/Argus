"""classical_poetry-vertical stage definitions.

The SECOND real literary vertical. Its deliverable is a 近体诗 (or 古体/词) that
(1) passes the machine prosody check (押韵/平仄/粘对/孤平/三平尾 — reproducible),
(2) has a real conception/立意 (human/live judgement), and (3) reads un-AI (守禁忌).

It CONSUMES the same four shared contracts fiction does — Task Envelope, Review,
Artifact Manifest, Provenance — via ``checks.py``; its craft state (prosody/诗体/
韵) is vertical-PRIVATE (``prosody.py``) and never lifted into the shared layer.

Stages (``completion_gate="none"`` — reviewer verdict ends the mission):

1. **intake**: record ``poetry/task_envelope.json`` + derive ``poetry/poem_brief.json``.
2. **form_plan**: choose 体裁/韵部/起承转合 -> ``poetry/form_plan.json``.
3. **compose**: write the poem -> ``poetry/draft_poem.txt``.
4. **prosody_check**: run the machine validator -> ``poetry/prosody_report.json``;
   the STAGE_CHECK FAILS on any 出韵/失替/三平尾/孤平.
5. **review**: reviewer emits ``poetry/review.json`` (prosody blocking + craft
   non-blocking) per the shared review contract.
6. **revise**: apply fixes -> ``poetry/final_poem.txt`` + ``poetry/revision_plan.json``
   + ``poetry/artifact_manifest.json``.
"""
from __future__ import annotations

from ...skills.stage_checklists import ChecklistItem

STAGE_ORDER = ["intake", "form_plan", "compose", "prosody_check", "review", "revise"]

completion_gate = "none"

_PIPELINE_CHECK = ("Pipeline state present", "test -f research/PIPELINE_STATE.json")
_CHECKS = "{python} -m argus_skill.verticals.classical_poetry.checks"

STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "intake": [
        _PIPELINE_CHECK,
        ("Task envelope recorded", "test -s poetry/task_envelope.json"),
        ("Task envelope valid and poetry-consumable",
         f"{_CHECKS} intake-validate poetry/task_envelope.json"),
        ("Poem brief produced", "test -s poetry/poem_brief.json"),
        ("Source registry is well-formed", f"{_CHECKS} source-registry"),
    ],
    "form_plan": [
        _PIPELINE_CHECK,
        ("Form/rhyme plan produced", "test -s poetry/form_plan.json"),
    ],
    "compose": [
        _PIPELINE_CHECK,
        ("Draft poem written", "test -s poetry/draft_poem.txt"),
    ],
    "prosody_check": [
        _PIPELINE_CHECK,
        ("Prosody report produced", "test -s poetry/prosody_report.json"),
        ("Poem passes the machine prosody check (押韵/平仄/孤平/三平尾)",
         f"{_CHECKS} prosody poetry/draft_poem.txt"),
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

_REVIEW_SKILL = "reviewer/prosody-and-conception-review.md"

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "intake": (
        _REVIEW_SKILL,
        "Confirm the poem brief was derived from poetry/task_envelope.json: the "
        "form is a classical-poetry form, the language is zh, and a 平声 韵部 target "
        "was chosen (not left to chance mid-poem).",
        ["poetry/task_envelope.json", "poetry/poem_brief.json"],
    ),
    "form_plan": (
        _REVIEW_SKILL,
        "Gate the plan before composing: 体裁(绝句/律诗·五/七言) fixed, a single 平声 "
        "韵部 chosen, and a 起承转合 with the 转 placed (颈联 or 绝句第三句).",
        ["poetry/form_plan.json", "poetry/poem_brief.json"],
    ),
    "compose": (
        _REVIEW_SKILL,
        "First read of the draft against the brief: right 体裁/句数/字数, on the "
        "chosen 韵部. Craft notes are non-blocking here.",
        ["poetry/draft_poem.txt", "poetry/poem_brief.json"],
    ),
    "prosody_check": (
        _REVIEW_SKILL,
        "Confirm the MACHINE prosody report: no 出韵/失替/三平尾/孤平 stands. This is "
        "the reproducible gate — do not re-judge metricality by ear, read the report.",
        ["poetry/prosody_report.json", "poetry/draft_poem.txt"],
    ),
    "review": (
        _REVIEW_SKILL,
        "Emit typed findings. PROSODY (rhyme/meter/hard_fault/parallelism) are "
        "BLOCKING and mirror the machine report. CRAFT (conception/imagery/diction/"
        "allusion/tone/anti_ai) are NON-BLOCKING live-reviewer judgements — is there "
        "a 立意 turn? a 诗眼? does the ending avoid slogan-style uplift? Never fake a "
        "numeric craft score.",
        ["poetry/draft_poem.txt", "poetry/prosody_report.json"],
    ),
    "revise": (
        _REVIEW_SKILL,
        "Verify every BLOCKING prosody finding is fixed (the final poem re-passes "
        "the machine check) with no NEW 出律 introduced, that revision_plan.json was "
        "derived from review.json, and that artifact_manifest.json records the chain "
        "(final supersedes the draft, traces back to draft + review).",
        ["poetry/final_poem.txt", "poetry/review.json", "poetry/revision_plan.json",
         "poetry/artifact_manifest.json"],
    ),
}

CHECKLIST_STAGE_ORDER = tuple(STAGE_ORDER)

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "prosody_check": (
        ChecklistItem(
            id="prosody-machine-clean",
            statement="The draft passes the machine prosody check: no 出韵, no 分明位 "
            "失替, no 三平尾, no 孤平.",
            evidence_hint="poetry/prosody_report.json compliant=true",
        ),
    ),
    "review": (
        ChecklistItem(
            id="prosody-blocking-mirrored",
            statement="Every machine prosody fault appears as a blocking finding; "
            "the reviewer does not silently pass an out-of-meter line.",
            evidence_hint="review.json blocking findings vs prosody_report",
        ),
        ChecklistItem(
            id="conception-is-live",
            statement="Conception/imagery/diction are recorded as NON-blocking "
            "live-reviewer judgements, never a faked machine score.",
            evidence_hint="review.json craft findings marked non-blocking",
        ),
    ),
}


def role_banner(role: str) -> str:
    """Hard-override framing per role — reframes the mission as a prosody-gated,
    conception-bearing classical poem, not a paper or a metric."""
    common = (
        "MISSION TYPE: CLASSICAL CHINESE POETRY. The deliverable is a 近体诗 "
        "(or 古体/词) that PASSES the machine prosody check (押韵/平仄/粘对/孤平/"
        "三平尾, reproducible), carries a real 立意, and reads un-AI. It is NOT a "
        "paper and NOT a metric.\n"
    )
    if role == "planner":
        return common + (
            "Drive intake -> form_plan -> compose -> prosody_check -> review -> "
            "revise. Fix 体裁 and a single 平声 韵部 in form_plan BEFORE composing."
        )
    if role == "engineer":
        return common + (
            "(1) Record poetry/task_envelope.json and derive the poem brief. "
            "(2) In form_plan fix 体裁(绝句/律诗·五/七言) and ONE 平声 韵部. (3) Compose "
            "on that 韵部, holding the 平仄 谱. (4) Run the machine prosody check and "
            "fix EVERY 出韵/失替/三平尾/孤平 before review — do not argue with the "
            "checker. (5) Record poetry/source_usage.json (empty uses[] if none) and "
            "poetry/artifact_manifest.json. (6) Avoid slogan endings and 陈词 imagery."
        )
    if role == "reviewer":
        return common + (
            "You gate the poem. PROSODY findings (rhyme/meter/hard_fault/parallelism) "
            "are BLOCKING and mirror the machine report — never pass an out-of-meter "
            "line. CRAFT (conception/imagery/diction/allusion/tone/anti_ai) are "
            "NON-BLOCKING live judgements, never a faked numeric score. Follow the "
            "'Prosody, Conception & Anti-AI Review' skill. Emit poetry/review.json as "
            "{verdict, findings[]} per the shared literary review contract."
        )
    return common
