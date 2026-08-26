"""Durable Planner-owned research-program state.

``RESEARCH_PLAN.md`` lives beside the continuous daemon's other state files.
Reads are deliberately fail-soft because a missing, partial, or concurrently
replaced document must never stop planning or mission execution.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

RESEARCH_PLAN_FILENAME = "RESEARCH_PLAN.md"
RESEARCH_PLAN_PROMPT_CHARS = 8_000
RESEARCH_PLAN_MISSION_CHARS = 2_000

_PLAN_SECTIONS = (
    "## Central hypotheses",
    "## Experiment program",
    "## Established results",
    "## Dead ends",
    "## Next milestone",
)
_NO_PLAN = (
    "(no plan yet — create RESEARCH_PLAN.md in this planning cycle from "
    "OBJECTIVE.md/the Manager mission brief and the journal)"
)


def research_plan_path(state_root: Path | str) -> Path:
    """Return the living-plan path under the daemon state root."""
    return Path(state_root) / RESEARCH_PLAN_FILENAME


def valid_research_plan(content: str) -> bool:
    """Whether *content* satisfies the minimum ordered Markdown contract."""
    text = str(content or "").strip()
    if not text.startswith("# Research plan\n"):
        return False
    hypotheses_at = text.find("## Central hypotheses")
    if hypotheses_at < 0:
        return False
    objective = text[len("# Research plan\n") : hypotheses_at].strip()
    if not objective or "\n" in objective:
        return False
    cursor = 0
    for heading in _PLAN_SECTIONS:
        position = text.find(heading, cursor)
        if position < 0:
            return False
        cursor = position + len(heading)
    return True


def read_research_plan(state_root: Path | str) -> str:
    """Read a valid plan, returning ``""`` for missing/corrupt state."""
    try:
        text = research_plan_path(state_root).read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return ""
    text = text.strip()
    return text if valid_research_plan(text) else ""


def _section(text: str, heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    following = [
        position
        for candidate in _PLAN_SECTIONS
        if candidate != heading
        and (position := text.find(candidate, start + len(heading))) >= 0
    ]
    end = min(following) if following else len(text)
    return text[start:end].strip()


def render_research_plan_for_planner(state_root: Path | str) -> str:
    """Render current state within the Planner's bounded dynamic allowance."""
    text = read_research_plan(state_root)
    if not text:
        return _NO_PLAN
    if len(text) <= RESEARCH_PLAN_PROMPT_CHARS:
        return text

    milestone = _section(text, "## Next milestone")
    # Preserve a useful tail without allowing a pathological final section to
    # consume the whole projection.
    milestone = milestone[:2_000].rstrip()
    notice = (
        "\n\n… [living plan truncated; return a pruned PLAN_UPDATE under "
        "~300 lines while preserving every Dead ends entry] …\n\n"
    )
    head_chars = max(
        0,
        RESEARCH_PLAN_PROMPT_CHARS - len(notice) - len(milestone),
    )
    return text[:head_chars].rstrip() + notice + milestone


def render_research_plan_for_mission(state_root: Path | str) -> str:
    """Project only hypotheses and the next paper-enabling milestone."""
    text = read_research_plan(state_root)
    if not text:
        return ""
    hypotheses = _section(text, "## Central hypotheses")
    milestone = _section(text, "## Next milestone")
    header = "## Research plan (mission excerpt)\n"
    limit = RESEARCH_PLAN_MISSION_CHARS - len(header)
    body = "\n\n".join(part for part in (hypotheses, milestone) if part)
    if len(body) > limit:
        # Keep both requested sections visible even when one is pathological.
        milestone_budget = min(len(milestone), max(500, limit // 3))
        hypotheses_budget = max(0, limit - milestone_budget - 2)
        if len(hypotheses) < hypotheses_budget:
            milestone_budget += hypotheses_budget - len(hypotheses)
            hypotheses_budget = len(hypotheses)
        hypotheses = hypotheses[: max(0, hypotheses_budget - 1)].rstrip() + "…"
        milestone = milestone[: max(0, milestone_budget - 1)].rstrip() + "…"
        body = hypotheses + "\n\n" + milestone
    return header + body


def replace_research_plan(state_root: Path | str, content: str) -> bool:
    """Atomically install a valid full plan; reject malformed updates."""
    text = str(content or "").strip()
    if not valid_research_plan(text):
        return False
    temporary: Path | None = None
    try:
        path = research_plan_path(state_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return True
    except OSError:
        try:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


__all__ = [
    "RESEARCH_PLAN_FILENAME",
    "RESEARCH_PLAN_MISSION_CHARS",
    "RESEARCH_PLAN_PROMPT_CHARS",
    "read_research_plan",
    "render_research_plan_for_mission",
    "render_research_plan_for_planner",
    "replace_research_plan",
    "research_plan_path",
    "valid_research_plan",
]
