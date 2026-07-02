"""The missing wire (缺失的那根线): recurring reviewer PROCESS lessons → a
synthesized reusable skill → the EXISTING SkillRouter admission gate.

EN: the reviewer emits a ``process_lesson`` every mission (how the agent worked,
a workaround that helped, where it wasted rounds). Until now that lesson was
journaled as ``self_evolve.process_lesson`` and surfaced to the Planner as TEXT,
but NEVER fed to skill creation — so argus produced genuine process data and
distilled ZERO skills from it (a 2-day self-hosted run: 9 real lessons, 0 skills).
This closes that loop: read the accumulated process_lessons, ask a runner to
synthesize the recurring ones into a GENERAL skill playbook, and route each
through :meth:`SkillRouter.apply_ops` so the SAME gates (mechanical well-formed +
cosine dedup vs the library + Manager generality/correctness) decide admission —
task-specific / duplicate / junk is rejected there, not here. Fail-soft: a distill
error never breaks a mission or a clean shutdown.

中文:reviewer 每个 mission 都产出 process_lesson,以前只 journal + 作为文本喂 Planner,
从不接到造 skill 上 —— 于是"过程数据有了、skill 一个没蒸馏出来"。这里把断线接上:读累积
的 process_lesson,让 runner 把反复出现的合成为通用 skill,再走**已有的** SkillRouter 门
(机械格式 + 去重 + Manager 通用性/正确性),垃圾/任务专属/重复由门拒掉。失败即静默。
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_LESSON_KIND = "self_evolve.process_lesson"
_SKILL_DELIM = "=====SKILL====="


def collect_process_lessons(journal: Any, *, limit: int = 40) -> list[str]:
    """Deduped ``process_lesson`` texts from the journal, newest-first. Fail-soft."""
    seen: set[str] = set()
    out: list[str] = []
    try:
        entries = list(journal.all())
    except Exception:  # noqa: BLE001
        return out
    for e in reversed(entries):
        if getattr(e, "kind", "") != _LESSON_KIND:
            continue
        extra = getattr(e, "extra", None) or {}
        text = str(extra.get("lesson") or getattr(e, "summary", "") or "").strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def synthesize_skills(
    runner: Any, lessons: list[str], *, max_skills: int = 3, reasoning_effort: str = "high",
) -> list[str]:
    """One LLM call: turn the RECURRING lessons into up to ``max_skills`` general
    skill playbooks (markdown). Returns the raw skill markdown blocks; [] on error
    or when nothing is generalizable. The blocks are validated downstream by the
    SkillRouter gate, so this only has to propose."""
    if not lessons or runner is None:
        return []
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lessons))
    prompt = (
        "You distill argus's own PROCESS lessons into reusable SKILLS. Below are "
        "process lessons the reviewer recorded across recent missions (how the "
        "agent worked, workarounds, wasted-round patterns).\n\n"
        f"{numbered}\n\n"
        f"Identify the ones that RECUR or share a common reusable pattern, and "
        f"write up to {max_skills} GENERAL skill playbook(s) capturing them — each "
        "applicable to a FAMILY of future tasks, NOT one specific task. SKIP "
        "one-offs and anything task-specific (paths/ids/numbers). If nothing is "
        "generalizable, output exactly NONE.\n\n"
        "Each skill MUST be markdown of the form:\n"
        "# <short imperative skill name>\n"
        "One-line description of when this applies.\n"
        "## When to use\n- ...\n## When NOT to use\n- ...\n## How to solve\n- steps\n\n"
        f"Separate multiple skills with a line containing exactly {_SKILL_DELIM}"
    )
    try:
        from ..core.models import RunnerOptions

        result = runner.run_exec(
            prompt=prompt,
            options=RunnerOptions(reasoning_effort=reasoning_effort, skip_git_repo_check=True),
            run_label="process-lesson-distill",
            resume_thread_id=None,
        )
        text = getattr(result, "last_agent_message", "") or getattr(result, "message", "") or ""
    except Exception as exc:  # noqa: BLE001 — synthesis must never break the caller
        log.debug("synthesize_skills failed: %s", exc)
        return []
    if not text.strip() or text.strip().upper().startswith("NONE"):
        return []
    blocks = [b.strip() for b in text.split(_SKILL_DELIM)]
    # keep only well-formed-looking blocks (a heading + a body); the router's
    # mechanical gate is the real validator, this just drops obvious noise.
    return [b for b in blocks if b and re.search(r"^#\s+\S", b, re.MULTILINE)][:max_skills]


def distill_process_lessons(
    *,
    journal: Any,
    router: Any,
    synth_runner: Any,
    min_lessons: int = 3,
    max_skills: int = 3,
    on_event: Any = None,
) -> dict:
    """THE WIRE. Collect recurring process_lessons → synthesize skills → route each
    through ``router.apply_ops`` (the existing admission gate). Returns a small
    summary. No-op when there are too few lessons or nothing generalizable; never
    raises.
    收集 → 合成 → 过已有的门。教训太少或无可通用即 no-op;绝不抛异常。
    """
    try:
        lessons = collect_process_lessons(journal)
        if len(lessons) < min_lessons:
            return {"created": 0, "reason": "insufficient lessons", "lessons": len(lessons)}
        contents = synthesize_skills(synth_runner, lessons, max_skills=max_skills)
        if not contents:
            return {"created": 0, "reason": "nothing generalizable", "lessons": len(lessons)}
        ops = [{"op": "create", "content": c} for c in contents]
        counts = router.apply_ops(ops, task="process-lesson distillation", on_event=on_event)
        counts = dict(counts or {})
        counts["candidates"] = len(contents)
        counts["lessons"] = len(lessons)
        return counts
    except Exception as exc:  # noqa: BLE001 — distillation is best-effort
        log.debug("distill_process_lessons failed: %s", exc)
        return {"created": 0, "reason": f"error: {type(exc).__name__}"}
