"""A second reading of the evidence.

When the Reviewer keeps saying that the current line of work should be
reconsidered, the people who built the plan are the wrong people to judge it:
they know what they hoped the evidence would show. A second reading is a fresh
look by someone who was not part of the work, with the files open and one
question in mind: what claim does the evidence collected so far actually
support? Its answer goes into the research notes, where every later turn will
read it, and to the Planner, which re-plans the rest of the project around it
and lets go of the tasks that belonged to the refuted idea.

The reading is commissioned sparingly: only after the Reviewer has asked for
reconsideration more than once since the last reading, and not more often than
every few hours, so that a single doubtful round does not trigger it.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Iterable

from .notes import notes_heading, read_research_notes, research_notes_path

log = logging.getLogger(__name__)

STATE_PATH = Path(".argus") / "SECOND_READING.json"
SECTION_TITLE = "A second reading of the evidence"
MIN_SIGNALS = 2
MIN_INTERVAL_SECONDS = 6.0 * 3600.0
_CLOSING_KEYS = ("REFUTED", "SUPPORTED", "NEXT")
_MAX_NOTES_CHARS = 24000


def _read_state(state_root: Path) -> dict[str, Any]:
    try:
        payload = json.loads((state_root / STATE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(state_root: Path, payload: dict[str, Any]) -> None:
    path = state_root / STATE_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        log.debug("second reading: could not persist state at %s", path, exc_info=True)


def note_reconsider_signal(state_root: Path, *, now: float | None = None) -> int:
    """Record that the Reviewer asked for reconsideration; return the running count."""
    state = _read_state(state_root)
    count = int(state.get("signals_since_reading") or 0) + 1
    state["signals_since_reading"] = count
    state["last_signal_at"] = time.time() if now is None else float(now)
    _write_state(state_root, state)
    return count


def second_reading_due(
    state_root: Path,
    *,
    now: float | None = None,
    min_signals: int = MIN_SIGNALS,
    min_interval_seconds: float = MIN_INTERVAL_SECONDS,
) -> bool:
    """Whether enough doubt has accumulated, and enough time passed, for a reading."""
    state = _read_state(state_root)
    if int(state.get("signals_since_reading") or 0) < min_signals:
        return False
    moment = time.time() if now is None else float(now)
    try:
        last = float(state.get("last_reading_at") or 0.0)
    except (TypeError, ValueError):
        last = 0.0
    return (moment - last) >= min_interval_seconds


def record_second_reading(state_root: Path, *, now: float | None = None) -> None:
    state = _read_state(state_root)
    state["signals_since_reading"] = 0
    state["last_reading_at"] = time.time() if now is None else float(now)
    state["readings"] = int(state.get("readings") or 0) + 1
    _write_state(state_root, state)


def build_second_reading_prompt(
    *,
    objective: str,
    stage: str,
    notes: str,
    challenge: str,
    alternative: str,
    pending_tasks: Iterable[tuple[str, str]],
    recent_reviews: Iterable[str],
) -> str:
    notes_text = (notes or "").strip()
    if len(notes_text) > _MAX_NOTES_CHARS:
        notes_text = notes_text[:_MAX_NOTES_CHARS].rstrip() + "\n[notes truncated]"
    tasks = "\n".join(f"- {task_id}: {title}" for task_id, title in pending_tasks) or "- (none)"
    reviews = "\n".join(f"- {text}" for text in recent_reviews if text) or "- (none)"
    return (
        "You are a researcher who has not been part of this project. You have "
        "been asked for a second reading of its evidence: the files are open in "
        "front of you, and the people who did the work will read what you write. "
        "Read the research notes below, the objective, and then the results the "
        "notes point to, including the raw rows behind the decisive comparisons; "
        "use the code and logs in this directory only to check what was actually "
        "run. Do not repeat the project's own summary back to it. Judge what the "
        "evidence shows.\n\n"
        f"## The objective\n{objective.strip() or '(none recorded)'}\n\n"
        f"## Current stage\n{stage or '(unknown)'}\n\n"
        f"## The research notes as they stand\n{notes_text or '(no notes yet)'}\n\n"
        f"## What the Reviewer has been saying\n{reviews}\n\n"
        f"## The Reviewer's latest doubt\n{challenge.strip() or '(not stated)'}\n"
        f"Its suggested alternative: {alternative.strip() or '(none)'}\n\n"
        f"## Tasks still planned\n{tasks}\n\n"
        "## What to write\n"
        "Write as a colleague would after a day with the files: plain, specific, "
        "and honest about strength of evidence. Cover, in prose, in this order:\n"
        "1. What the evidence collected so far supports, stated as the claim a "
        "careful paper could make today, with the comparisons that carry it and "
        "at what scale they were run.\n"
        "2. What the evidence has refuted or failed to support, and which of the "
        "planned tasks belong to that refuted line (name them by id).\n"
        "3. What remains open, and the single most decisive next experiment, "
        "sized to the claim and to the machine this project runs on.\n"
        "Say if the honest answer is that the evidence supports a narrower or "
        "different claim than the one being pursued; that is the most useful "
        "thing a second reading can say. Do not use workflow vocabulary.\n\n"
        "End with exactly these three lines, each one sentence:\n"
        "REFUTED=<what the evidence has refuted, or 'nothing yet'>\n"
        "SUPPORTED=<the claim the evidence supports today>\n"
        "NEXT=<the single most decisive next experiment>"
    )


def parse_second_reading(raw: str) -> dict[str, str]:
    """Split the reading into its prose and its three closing lines."""
    text = str(raw or "").strip()
    closing: dict[str, str] = {}
    body_lines: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s*(REFUTED|SUPPORTED|NEXT)\s*=\s*(.*)$", line)
        if match:
            closing[match.group(1)] = match.group(2).strip()
            continue
        body_lines.append(line)
    body = "\n".join(body_lines).strip()
    return {
        "body": body,
        "refuted": closing.get("REFUTED", ""),
        "supported": closing.get("SUPPORTED", ""),
        "next": closing.get("NEXT", ""),
    }


def insert_second_reading_into_notes(
    project_root: Path,
    *,
    body: str,
    supported: str,
    next_step: str,
    when: float | None = None,
) -> Path:
    """Place the reading at the top of the research notes, replacing any earlier one."""
    from ...manager.source_writeback import atomic_write

    moment = time.time() if when is None else float(when)
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(moment))
    section_lines = [f"## {SECTION_TITLE} ({stamp})", ""]
    if supported:
        section_lines += [f"What the evidence supports today: {supported}", ""]
    if next_step:
        section_lines += [f"The most decisive next experiment: {next_step}", ""]
    section_lines += [body.strip(), ""]
    section = "\n".join(section_lines)

    existing = read_research_notes(project_root)
    lines = existing.splitlines()
    heading = lines[0] if lines and lines[0].startswith("# ") else notes_heading("")
    rest = lines[1:] if lines and lines[0].startswith("# ") else lines
    # Drop an earlier reading so the notes carry only the latest one.
    kept: list[str] = []
    skipping = False
    for line in rest:
        if line.startswith(f"## {SECTION_TITLE}"):
            skipping = True
            continue
        if skipping and line.startswith("## "):
            skipping = False
        if not skipping:
            kept.append(line)
    body_text = "\n".join(kept).strip("\n")
    text = f"{heading}\n\n{section}\n" + (f"{body_text}\n" if body_text else "")
    path = research_notes_path(project_root)
    atomic_write(path, text)
    return path


__all__ = [
    "MIN_INTERVAL_SECONDS",
    "MIN_SIGNALS",
    "SECTION_TITLE",
    "STATE_PATH",
    "build_second_reading_prompt",
    "insert_second_reading_into_notes",
    "note_reconsider_signal",
    "parse_second_reading",
    "record_second_reading",
    "second_reading_due",
]
