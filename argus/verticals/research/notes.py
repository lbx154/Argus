"""The research notes: the account of where the work stands.

At the end of each stage the role that finished it writes ``RESEARCH_NOTES.md``
at the project root for whoever continues the work: the thesis as the evidence
now supports it, the decisive comparisons, the strongest baseline, and what
comes next.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

RESEARCH_NOTES_FILENAME = "RESEARCH_NOTES.md"

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
    """Return the notes path."""
    return Path(project_root) / RESEARCH_NOTES_FILENAME


def read_research_notes(project_root: Path | str) -> str:
    """Return the notes text, or an empty string when there are none."""
    try:
        return research_notes_path(project_root).read_text(encoding="utf-8")
    except OSError:
        return ""


def clear_research_notes(project_root: Path | str) -> None:
    """Remove the notes; used when the objective changes."""
    path = research_notes_path(project_root)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.debug("research notes: could not clear %s", path, exc_info=True)


__all__ = [
    "RESEARCH_NOTES_FILENAME",
    "clear_research_notes",
    "notes_heading",
    "read_research_notes",
    "research_notes_path",
]
