"""A resumed role thread is not sent the sections it already holds verbatim."""
from __future__ import annotations

from argus.core.role_session import elide_repeated_sections

CHECKLIST = "## Stage checklist\n" + "- keep the evaluation split fixed\n" * 30
VOICE = "## How to write\n" + "Write for the reader who was not here.\n" * 20


def _prompt(reality: str) -> str:
    return "\n\n".join([
        "## Continued Planner cycle\nYou are resuming your own session.",
        CHECKLIST,
        "## Current reality\n" + reality,
        VOICE,
    ]) + "\n"


def test_changed_short_and_fenced_sections_stay() -> None:
    changed = CHECKLIST + "- also report the variance\n"
    fenced = "## Example\n```\n## not a heading\n```\n" + "x" * 500
    previous = _prompt("same") + "\n" + fenced
    current = _prompt("same").replace(CHECKLIST, changed) + "\n" + fenced

    sent = elide_repeated_sections(current, previous)

    assert "also report the variance" in sent
    assert "## Current reality\nsame" in sent, "short sections are cheaper inline"
    assert "`Example`" in sent and "## not a heading" not in sent
    assert elide_repeated_sections(_prompt("a"), "") == _prompt("a")
