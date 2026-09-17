"""The research notes: the account of where the work stands.

At the end of each stage the role that finished it writes ``RESEARCH_NOTES.md``
at the project root for whoever continues the work: the thesis as the evidence
now supports it, the decisive comparisons, the strongest baseline, and what
comes next. Earlier versions of Argus called this file ``HANDOFF.md``; a
project that still carries the old name is moved to the new one the first time
Argus reads it, so a campaign that began under the old name continues without
interruption.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

RESEARCH_NOTES_FILENAME = "RESEARCH_NOTES.md"
LEGACY_NOTES_FILENAME = "HANDOFF.md"

_STAGE_TITLES = {
    "idea": "Idea stage",
    "experiment": "Experiment stage",
    "paper": "Paper stage",
}


def notes_heading(stage: str) -> str:
    """First line of the research notes written at the end of ``stage``."""
    title = _STAGE_TITLES.get(str(stage or "").strip().lower(), "")
    return f"# Research notes — {title}" if title else "# Research notes"


def research_notes_path(project_root: Path | str) -> Path:
    """Return the notes path, moving a legacy ``HANDOFF.md`` into place first."""
    root = Path(project_root)
    current = root / RESEARCH_NOTES_FILENAME
    legacy = root / LEGACY_NOTES_FILENAME
    if not current.exists() and legacy.exists():
        try:
            legacy.replace(current)
            log.info("research notes: moved %s to %s", legacy, current)
        except OSError:
            log.debug("research notes: could not move %s", legacy, exc_info=True)
            return legacy
    return current


def read_research_notes(project_root: Path | str) -> str:
    """Return the notes text, or an empty string when there are none."""
    try:
        return research_notes_path(project_root).read_text(encoding="utf-8")
    except OSError:
        return ""


def clear_research_notes(project_root: Path | str) -> None:
    """Remove the notes under both names; used when the objective changes."""
    root = Path(project_root)
    for name in (RESEARCH_NOTES_FILENAME, LEGACY_NOTES_FILENAME):
        try:
            (root / name).unlink(missing_ok=True)
        except OSError:
            log.debug("research notes: could not clear %s", root / name, exc_info=True)


__all__ = [
    "LEGACY_NOTES_FILENAME",
    "RESEARCH_NOTES_FILENAME",
    "clear_research_notes",
    "notes_heading",
    "read_research_notes",
    "research_notes_path",
]
