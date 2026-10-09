"""The independent Reviewer's own earlier findings, and only those.

Two guarantees for later rounds:

* Carry-over: a Reviewer that starts a fresh provider thread (rotation, a
  changed rubric, a fresh-only backend) still sees what it found before, in
  full rather than cut at 600 characters, and sees it once rather than three
  times beside the MissionBrief copies.
* Provenance: only judgments the Reviewer call itself returned count as its
  findings, quoted in its own words. Whatever the host did instead (a
  placeholder for an unjudged round, a replaced or rewritten verdict) is shown
  as a separately labelled host fact and never as the Reviewer's judgment.
  The Engineer's own text (such as a turn-limit wind-down summary) is never
  shown here at all.

Earlier words are presented as earlier words: the Reviewer is told it may
find them wrong and correct them. Nothing here judges the work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from ..core.model_visible_text import sanitize_model_visible_text
from ..core.models import ReviewDecision, RoundRecord
from ..core.secret_guard import known_secret_values, redact_secrets_text

_REASON_LIMIT = 2000
_ACTION_LIMIT = 1200
# Same bounds the MissionBrief used for its item lists.
_ITEMS = 6
_ITEM_LIMIT = 400
_EARLIER = 2
_EARLIER_LIMIT = 300
_HOST_FACTS = 4

_UNJUDGED = {
    "provider_turn_cap": "the Engineer's session reached its per-call turn limit",
    "backend_failure": "the model service dropped the Engineer's session",
    "silent_command": (
        "Argus stopped the Engineer's session after a command stayed silent "
        "for the whole idle limit"
    ),
}

_EARLIER_WORDS_NOTE = (
    "These are your earlier words, not verified facts. If current evidence "
    "shows one was wrong, say so and correct it."
)


@dataclass(frozen=True)
class PreviousFindings:
    """Rendered carry-over plus what it already covers."""

    text: str = ""
    has_reviewer_findings: bool = False
    has_open_items: bool = False

    def __bool__(self) -> bool:
        return bool(self.text)


def reviewer_authored_rounds(rounds: Sequence[RoundRecord]) -> list[RoundRecord]:
    """Rounds whose recorded judgment came from the independent Reviewer call."""
    return [
        record for record in rounds
        if record.review.reviewer_authored
        and (record.review.review_source or "reviewer") == "reviewer"
    ]


def _one_line(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _clip(value: object, limit: int) -> str:
    """Keep the author's line breaks; tidy spacing; stay within ``limit``."""
    lines: list[str] = []
    for raw in str(value or "").strip().splitlines():
        line = " ".join(raw.split())
        if not line and (not lines or not lines[-1]):
            continue
        lines.append(line)
    text = "\n".join(lines).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def reviewer_words_of(review: ReviewDecision) -> tuple[str, str, str]:
    """The Reviewer's own status, reason and request, before any host rewrite."""
    words = review.reviewer_words if isinstance(review.reviewer_words, dict) else {}
    status = str(words.get("status", review.status) or "")
    reason = str(words.get("reason", review.reason) or "")
    action = str(words.get("next_action", review.next_action) or "")
    return status, reason, action


def _items(frontier: object, key: str) -> tuple[list[str], int]:
    if not isinstance(frontier, dict):
        return [], 0
    value = frontier.get(key)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return [], 0
    items = list(dict.fromkeys(
        text for item in value if (text := _one_line(item, _ITEM_LIMIT))
    ))
    return items[:_ITEMS], max(0, len(items) - _ITEMS)


def _item_lines(title: str, items: list[str], hidden: int) -> list[str]:
    if not items:
        return []
    lines = [title, *(f"- {item}" for item in items)]
    if hidden:
        lines.append(f"- (+{hidden} more not shown)")
    return lines


def _earlier(records: Iterable[RoundRecord]) -> list[str]:
    earlier = list(records)[-_EARLIER:]
    if not earlier:
        return []
    lines = ["Your earlier judgments:"]
    for record in earlier:
        status, reason, _ = reviewer_words_of(record.review)
        lines.append(
            f"- Round {record.round_index} — {_one_line(status, 40) or 'unknown'}: "
            f"{_one_line(reason, _EARLIER_LIMIT) or '(no reason)'}"
        )
    return lines


def _unjudged_line(record: RoundRecord) -> str:
    review = record.review
    index = record.round_index
    if review.host_placeholder in _UNJUDGED:
        return f"Round {index} was not reviewed: {_UNJUDGED[review.host_placeholder]}."
    if review.host_placeholder == "operator_question_policy":
        return (
            f"Round {index}: the host replaced the review because operator "
            "questions are not allowed in this mission."
        )
    if (review.review_source or "reviewer") == "engineer_self_review":
        return f"Round {index} was checked only by the Engineer's self-review, not by you."
    return f"Round {index} has no judgment of yours; the host recorded it."


def _host_facts(rounds: Sequence[RoundRecord], latest: RoundRecord | None) -> list[str]:
    facts: list[str] = []
    if latest is not None:
        facts.extend(
            f"After your round {latest.round_index} judgment, the host noted: "
            + _one_line(note, _ITEM_LIMIT) + "."
            for note in latest.review.host_notes if str(note or "").strip()
        )
    start = latest.round_index if latest is not None else 0
    facts.extend(
        _unjudged_line(record) for record in rounds
        if record.round_index > start and record is not latest
        and not record.review.reviewer_authored
    )
    return facts[-_HOST_FACTS:]


def render_previous_findings(
    rounds: Sequence[RoundRecord], *, round_index: int,
) -> PreviousFindings:
    """The Reviewer's own previous findings and the host facts since them.

    A first round, or a later one with no earlier record at all, gets an empty
    result and therefore the unchanged prompt.
    """
    if round_index <= 1 or not rounds:
        return PreviousFindings()
    reviewed = reviewer_authored_rounds(rounds)
    latest = reviewed[-1] if reviewed else None
    lines: list[str] = []
    has_open_items = False
    if latest is not None:
        status, reason, action = reviewer_words_of(latest.review)
        lines += [
            "## Your previous findings",
            _EARLIER_WORDS_NOTE,
            f"Your judgment in round {latest.round_index} "
            f"(`{_one_line(status, 40) or 'unknown'}`):",
        ]
        reason_text = _clip(reason, _REASON_LIMIT)
        action_text = _clip(action, _ACTION_LIMIT)
        if reason_text:
            lines.append(reason_text)
        if action_text and action_text != reason_text and not reason_text.startswith(
            action_text[:200]
        ):
            lines.append("What you asked for:\n" + action_text)
        frontier = latest.review.frontier_report
        lines += _item_lines("Evidence you cited:", *_items(frontier, "evidence"))
        open_items, hidden = _items(frontier, "remaining_work")
        has_open_items = bool(open_items)
        lines += _item_lines("Open items you named:", open_items, hidden)
        lines += _earlier(reviewed[:-1])
    facts = _host_facts(rounds, latest)
    if facts:
        if lines:
            lines.append("")
        lines += [
            "## Host facts since then (not your words)",
            *(f"- {fact}" for fact in facts),
        ]
    if not lines:
        return PreviousFindings()
    text = sanitize_model_visible_text(
        redact_secrets_text("\n".join(lines), known_values=known_secret_values())
    )
    return PreviousFindings(
        text=text,
        has_reviewer_findings=latest is not None,
        has_open_items=has_open_items,
    )


def own_reviewer_thread(
    resume_thread_id: str | None,
    *,
    foreign_thread_ids: Iterable[str | None],
) -> str | None:
    """Return the Reviewer's resume id only when it is no other role's thread.

    The Reviewer's independence rests on never continuing the Engineer's
    conversation. Capsules are already per-role, so this guards against a
    corrupted or mis-seeded capsule rather than an expected path.
    """
    if not resume_thread_id:
        return None
    foreign = {str(item) for item in foreign_thread_ids if item}
    return None if str(resume_thread_id) in foreign else resume_thread_id


__all__ = [
    "PreviousFindings",
    "own_reviewer_thread",
    "render_previous_findings",
    "reviewer_authored_rounds",
    "reviewer_words_of",
]
