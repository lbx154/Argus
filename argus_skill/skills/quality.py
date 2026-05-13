"""Programmatic quality gate for distilled playbooks.

The scientist sometimes produces playbooks that look fine to a human
but are useless as cached capabilities — bloated, single-task-specific,
or missing required structure. Rather than trust the output blindly,
``check_skill_quality`` runs a deterministic checklist and either
accepts the playbook or rejects it with a list of human-readable
reasons. The caller (``SkillStore.save_distilled``) then decides
whether to persist or discard.

The bar is intentionally tighter than the prompt's own constraints —
the prompt says "stay general", the gate enforces it. Without a gate
the prompt is a suggestion the model may or may not follow.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Hard limits.
MAX_WORDS = 900
MIN_WHEN_NOT_TO_USE_BULLETS = 3
MIN_EXAMPLE_BULLETS = 2

REQUIRED_HEADINGS = (
    "Title",
    "Description",
    "Category",
    "When to use",
    "When NOT to use",
    "How to solve",
    "Examples",
    "Response shape",
)


@dataclass
class QualityReport:
    ok: bool
    reasons: list[str]
    warnings: list[str]
    word_count: int

    def render(self) -> str:
        if self.ok:
            warn = ("  (warnings: " + "; ".join(self.warnings) + ")"
                    if self.warnings else "")
            return f"quality OK ({self.word_count} words){warn}"
        return "quality REJECTED: " + "; ".join(self.reasons)


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _has_heading(content: str, heading: str) -> bool:
    pattern = rf"(?im)^\s*#{{1,6}}\s+{re.escape(heading)}\s*$"
    return re.search(pattern, content) is not None


def _section_body(content: str, heading: str) -> str:
    pattern = (
        rf"(?ims)^\s*#{{1,6}}\s+{re.escape(heading)}\s*$"
        r"(.*?)"
        rf"(?=^\s*#{{1,6}}\s+\S|\Z)"
    )
    m = re.search(pattern, content)
    return m.group(1) if m else ""


def _count_bullets(body: str) -> int:
    return sum(1 for line in body.splitlines() if line.lstrip().startswith(("-", "*")))


def _looks_task_specific(body: str, task_description: str) -> list[str]:
    """Detect concrete tokens from the task that leaked into the body.

    We look for capitalised PascalCase identifiers, snake_case names
    >=4 chars, and quoted file paths, and flag any of them that show up
    verbatim in the playbook body. These are nearly always single-task
    parameters that should have been replaced with ``<placeholder>``.
    """
    leaks: list[str] = []
    body_norm = body.lower()
    candidates: set[str] = set()

    # Quoted strings inside backticks or quotes.
    for m in re.finditer(r"`([^`]{3,40})`", task_description):
        candidates.add(m.group(1))
    for m in re.finditer(r'"([^"]{3,40})"', task_description):
        candidates.add(m.group(1))

    # File paths (slash-bearing tokens)
    for m in re.finditer(r"(?:/?[\w\-.]+/){1,}[\w\-.]+", task_description):
        candidates.add(m.group(0))

    # snake_case_with_underscores / kebab-case-with-dashes (>=2 segments)
    for m in re.finditer(r"\b[a-z][a-z0-9]*(?:[_-][a-z0-9]+){1,}\b", task_description):
        candidates.add(m.group(0))

    seen: set[str] = set()
    for c in candidates:
        if len(c) < 4:
            continue
        # Skip generic English snake_case like "step_by_step".
        if c.lower() in {"step_by_step", "from_scratch", "must_hold", "all_must_hold"}:
            continue
        if c.lower() in seen:
            continue
        seen.add(c.lower())
        # Allow up to one mention (might appear in "Examples" by design);
        # multiple mentions across body strongly suggest hardcoding.
        occurrences = body_norm.count(c.lower())
        if occurrences >= 2:
            leaks.append(c)
    return leaks[:5]


def check_skill_quality(
    *,
    raw_distill_output: str,
    task_description: str,
) -> QualityReport:
    """Validate a distilled playbook against the cache contract."""
    reasons: list[str] = []
    warnings: list[str] = []
    text = raw_distill_output or ""

    # 1. Required headings (case-insensitive). Anything missing breaks
    # downstream parsing & rendering, so this is a hard reject.
    missing = [h for h in REQUIRED_HEADINGS if not _has_heading(text, h)]
    if missing:
        reasons.append(f"missing headings: {', '.join(missing)}")

    # 2. Word count (anti-bloat).
    wc = _word_count(text)
    if wc > MAX_WORDS:
        reasons.append(f"too long: {wc} words > {MAX_WORDS}")
    elif wc < 80:
        reasons.append(f"too short: {wc} words")

    # 3. Anti-conditions must list >=3 bullets.
    when_not = _section_body(text, "When NOT to use")
    if _count_bullets(when_not) < MIN_WHEN_NOT_TO_USE_BULLETS:
        reasons.append(
            f"`When NOT to use` has fewer than "
            f"{MIN_WHEN_NOT_TO_USE_BULLETS} bullets"
        )

    # 4. Examples must list >=2 distinct sketches (so the family is
    # demonstrated, not just paraphrased from the source task).
    examples = _section_body(text, "Examples")
    n_ex = _count_bullets(examples)
    if n_ex < MIN_EXAMPLE_BULLETS:
        reasons.append(
            f"`Examples` has fewer than {MIN_EXAMPLE_BULLETS} bullets "
            f"(found {n_ex})"
        )

    # 5. Task-specific leakage. Warning, not rejection — sometimes the
    # task domain shares vocabulary with the family ("cli" is fine).
    body_for_leak = (
        _section_body(text, "Description")
        + _section_body(text, "When to use")
        + _section_body(text, "How to solve")
    )
    leaks = _looks_task_specific(body_for_leak, task_description)
    if leaks:
        warnings.append("possible hardcoded task tokens: " + ", ".join(leaks))

    return QualityReport(
        ok=not reasons,
        reasons=reasons,
        warnings=warnings,
        word_count=wc,
    )


__all__ = [
    "QualityReport",
    "check_skill_quality",
    "MAX_WORDS",
    "REQUIRED_HEADINGS",
]
