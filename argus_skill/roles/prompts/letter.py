"""The letter Argus writes to the operator while they are away.

A campaign runs for days. The operator should be able to open the project in
the morning and read, in a few paragraphs, what happened while they were gone:
what was tried and found, what was decided and why, what is running now, what
gave pause, and what would help. The facts come from the host; the letter is
written by the Manager in the operator's language.
"""
from __future__ import annotations

from typing import Any, Mapping


def build_letter_prompt(*, facts: Mapping[str, Any]) -> str:
    """Render the prompt from which the Manager writes the letter."""
    language = "Chinese" if facts.get("chinese") else "English"
    hours_away = facts.get("hours_since_operator_wrote")
    away = (
        f"about {hours_away:.0f} hours" if isinstance(hours_away, (int, float)) and hours_away >= 1
        else "a while"
    )
    since = facts.get("hours_since_last_letter")
    window = (
        f"the last {since:.0f} hours" if isinstance(since, (int, float)) and since >= 1
        else "since the campaign began"
    )

    def section(title: str, key: str) -> str:
        value = str(facts.get(key) or "").strip()
        return f"### {title}\n{value or '(nothing recorded)'}\n"

    return (
        "You are writing to the person who set this research going. They have "
        f"been away for {away} and will read this when they return. Write them a "
        f"letter in {language}, as a colleague who kept working while they were "
        "gone would write: what you did and found, what you decided and why, "
        "what is running now, what gave you pause, and what you would like them "
        "to look at or answer, only if it truly needs them. Close with what will "
        "happen next if they say nothing.\n\n"
        "Use only the facts below; do not invent results, numbers, or file names. "
        "Current tasks, checkpoints and waiting states take precedence over "
        "historical settlements. A healthy background wait continues automatically; "
        "it is not a failed experiment or a request for operator permission. "
        "The saved review describes the version reviewed, not acceptance of newer "
        "unreviewed work. Treat old failure and plan-change summaries as history, "
        "not as current Reviewer feedback. Ask the operator to answer only a "
        "question explicitly listed as currently waiting on them; when that "
        "section is empty, do not invent a clarification or say work needs their "
        "reply. Lead with the scientific progress and actual next work, not "
        "mission counters or internal control records. "
        "Where a number matters, give it; where it does not, leave it out. Speak "
        "plainly and warmly, in complete sentences, three to six short paragraphs, "
        "with no headings and no lists except when naming files to open. Do not "
        "use workflow vocabulary or internal role names; say 'I' for the work "
        "Argus did. Sign as Argus.\n\n"
        f"## Facts from {window}\n"
        + section("The objective", "objective")
        + section("Where the work stands", "stage_line")
        + section("Current tasks and saved progress", "current_work")
        + section("Running now, and waiting on", "running")
        + section("Questions currently waiting on the operator", "questions")
        + section("The research notes, as they begin", "notes_head")
        + section("Latest saved review of the reviewed version", "latest_review")
        + section("Historical mission settlements in this window", "missions")
        + section("Historical decisions taken", "decisions")
        + section("Planned next", "planned")
        + section("Cost and time in this window", "cost")
        + section("The machine", "machine")
        + "\nWrite the letter now."
    )


__all__ = ["build_letter_prompt"]
