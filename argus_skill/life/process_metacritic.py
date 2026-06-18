"""Process self-distillation — STEP 2: the process meta-critic.

A read-only LLM auditor that turns the quantified corpus process ledger
(``process_distill.aggregate_ledgers``) + excerpts of the agent's OWN scaffolding source
into structured, reusable PROCESS lessons. It changes the agent's PROCESS only and NEVER
the outcome definition (metric / verifier / what-counts-as-winning stays frozen and
off-limits). It APPLIES NOTHING — every lesson is born ``status="shadow"``; the apply path
(overlay + counterfactual replay + A/B) stays operator-gated.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core.models import RunnerOptions
from ..core.ports import RunnerBackend

METACRITIC_INSTRUCTION = """\
You are a READ-ONLY PROCESS META-CRITIC auditing an autonomous research agent's OWN
process. You may diagnose and propose changes to the agent's PROCESS (its prompts, gates,
stall logic, lesson-extraction triggers) but NEVER to its OUTCOME definition — the metric,
the verifier, the validity test, and "what counts as winning" are FROZEN and off-limits.
You APPLY NOTHING; this is a shadow audit.

You are given a CORPUS PROCESS LEDGER (quantified signals across many real missions) and
EXCERPTS of the agent's own scaffolding source (the incentive surface). Reason from the
NUMBERS and cite exact file:line for every source claim.

Find:
1. The DOMINANT process pathology — name it and QUANTIFY it from the ledger.
2. The INCENTIVE CONTRADICTION driving it — where a prose layer EXHORTS one behaviour
   while a machine-filled signal / hard counter / mis-aimed trigger REWARDS or PUNISHES
   the opposite. Cite BOTH sides (file:line). A learning trigger aimed at a rare cause
   while the dominant failure cause goes unharvested is exactly this.
3. The SMALLEST reversible PROCESS fix (file:line). Process only — never the outcome.

Output ONLY a JSON array (no prose around it). Each element:
{"dominant_pattern": str, "incentive_contradiction": str,
 "evidence": str, "proposed_process_fix": str}
"""


@dataclass
class ProcessLesson:
    id: str
    dominant_pattern: str
    incentive_contradiction: str
    evidence: str
    proposed_process_fix: str
    n_missions: int = 0
    status: str = "shadow"  # shadow → (operator) proposed → applied/rejected. Never auto-applied here.

    def to_dict(self) -> dict:
        return asdict(self)


def _lesson_id(d: dict) -> str:
    seed = (d.get("dominant_pattern", "") + "||" + d.get("proposed_process_fix", "")).encode()
    return hashlib.sha1(seed).hexdigest()[:12]


def build_metacritic_prompt(corpus_ledger: dict, incentive_excerpts: dict[str, str]) -> str:
    surface = "\n\n".join(
        f"### {label}\n{text.strip()}" for label, text in incentive_excerpts.items()
    )
    return (
        METACRITIC_INSTRUCTION
        + "\n\n## CORPUS PROCESS LEDGER\n```json\n"
        + json.dumps(corpus_ledger, ensure_ascii=False, indent=1)
        + "\n```\n\n## INCENTIVE SURFACE (agent's own scaffolding source)\n"
        + (surface or "(none provided)")
        + "\n"
    )


def parse_lessons(text: str, *, n_missions: int = 0) -> list[ProcessLesson]:
    """Extract the JSON array of lessons from the meta-critic's output (tolerant of
    surrounding prose / code fences)."""
    if not text:
        return []
    # strip code fences, then grab the outermost JSON array
    cleaned = re.sub(r"```(?:json)?", "", text)
    m = re.search(r"\[.*\]", cleaned, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out: list[ProcessLesson] = []
    for d in arr:
        if not isinstance(d, dict):
            continue
        out.append(ProcessLesson(
            id=_lesson_id(d),
            dominant_pattern=str(d.get("dominant_pattern", "")).strip(),
            incentive_contradiction=str(d.get("incentive_contradiction", "")).strip(),
            evidence=str(d.get("evidence", "")).strip(),
            proposed_process_fix=str(d.get("proposed_process_fix", "")).strip(),
            n_missions=n_missions,
        ))
    return out


def distill_process_lessons(
    backend: RunnerBackend,
    corpus_ledger: dict,
    *,
    incentive_excerpts: dict[str, str] | None = None,
    options: RunnerOptions | None = None,
) -> list[ProcessLesson]:
    """Run ONE read-only meta-critic pass over the corpus ledger → shadow process lessons."""
    prompt = build_metacritic_prompt(corpus_ledger, incentive_excerpts or {})
    result = backend.run_exec(
        prompt=prompt,
        options=options or RunnerOptions(),
        run_label="process_metacritic",
    )
    # ``message`` is a @property on the real RunnerResult (str) but a method on
    # some test doubles — tolerate both, then fall back to agent_messages.
    _msg = getattr(result, "message", None)
    text = (_msg() if callable(_msg) else (_msg or ""))
    if not text and getattr(result, "agent_messages", None):
        text = result.agent_messages[-1]
    return parse_lessons(text, n_missions=int(corpus_ledger.get("n_missions", 0) or 0))


def persist_lessons(lessons: list[ProcessLesson], out_dir: str | Path, meta_epoch: str) -> Path:
    """Write shadow lessons to the process-lessons store (reviewable, never auto-applied)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fp = out / f"process_lessons_{meta_epoch}.json"
    fp.write_text(
        json.dumps([le.to_dict() for le in lessons], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return fp
