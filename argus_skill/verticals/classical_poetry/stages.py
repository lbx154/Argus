"""classical_poetry-vertical stage definitions.

The SECOND real literary vertical. Its deliverable is a 近体诗 (or 古体/词) that
(1) passes the machine prosody check (押韵/平仄/粘对/孤平/三平尾 — reproducible),
(2) has a real conception/立意 (human/live judgement), and (3) reads un-AI (守禁忌).

Its craft state (prosody/诗体/韵) is vertical-PRIVATE (``prosody.py``) and never
lifted into the shared layer.

Stages (``completion_gate="none"`` — reviewer verdict ends the mission):

1. **intake**: record ``poetry/task_envelope.json`` + derive ``poetry/poem_brief.json``.
2. **form_plan**: choose 体裁/韵部/起承转合 -> ``poetry/form_plan.json``.
3. **compose**: write the poem -> ``poetry/draft_poem.txt``.
4. **prosody_check**: run the machine validator -> ``poetry/prosody_report.json``;
   stage completion fails on any 出韵/失替/三平尾/孤平.
5. **review**: reviewer emits ``poetry/review.json`` (prosody blocking + craft
   non-blocking) per the shared review contract.
6. **revise**: apply fixes -> ``poetry/final_poem.txt`` + ``poetry/revision_plan.json``
   + ``poetry/artifact_manifest.json``.
"""
from __future__ import annotations

from pathlib import Path

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ["intake", "form_plan", "compose", "prosody_check", "review", "revise"]
CHECKLIST_OPTIONAL_STAGES = ("intake", "form_plan", "compose", "revise")

completion_gate = "none"

def stage_completion_issues(stage: str, project_root: Path) -> tuple[str, ...]:
    if stage in {"prosody_check", "revise"}:
        from .prosody import analyze

        name = "draft_poem.txt" if stage == "prosody_check" else "final_poem.txt"
        try:
            result = analyze(
                (project_root / "poetry" / name).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            return (f"classical poetry {name} invalid: {exc}",)
        return tuple(
            finding["detail"]
            for finding in result["findings"]
            if finding["severity"] == "blocking"
        )
    return ()

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
