"""The independent Reviewer's own earlier findings, and only those.

Two guarantees for later rounds:

* Carry-over: a Reviewer that starts a fresh provider thread (rotation, a
  changed rubric, a fresh-only backend) still sees what it found before, in
  full rather than cut at 600 characters, and sees it once rather than three
  times beside the MissionBrief copies.
* Provenance: only judgments the Reviewer call itself returned count as its
  findings. Host placeholders written when the Engineer's turn failed, hit its
  turn allowance, or was stopped for silence (some carrying the Engineer's own
  wind-down text) and Engineer self-reviews are never shown as the Reviewer's
  previous judgment, and never become settled context for the re-review.

Nothing here judges the work; it only selects and renders the Reviewer's own
words.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from ..core.model_visible_text import sanitize_model_visible_text
from ..core.models import RoundRecord
from ..core.secret_guard import known_secret_values, redact_secrets_text

_REASON_LIMIT = 2000
_ACTION_LIMIT = 1200
_ITEMS = 4
_ITEM_LIMIT = 300
_EARLIER = 2
_EARLIER_LIMIT = 300


def independent_review_rounds(rounds: Sequence[RoundRecord]) -> list[RoundRecord]:
    """Rounds whose recorded judgment came from the independent Reviewer call."""
    return [record for record in rounds if record.review.independent_review]


def _one_line(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _items(frontier: object, key: str) -> list[str]:
    if not isinstance(frontier, dict):
        return []
    value = frontier.get(key)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [_one_line(item, _ITEM_LIMIT) for item in value[:_ITEMS] if str(item or "").strip()]


def _earlier(records: Iterable[RoundRecord]) -> list[str]:
    earlier = list(records)[-_EARLIER:]
    if not earlier:
        return []
    lines = ["Your earlier judgments:"]
    for record in earlier:
        status = _one_line(record.review.status, 40) or "unknown"
        reason = _one_line(record.review.reason, _EARLIER_LIMIT) or "(no reason)"
        lines.append(f"- Round {record.round_index} — {status}: {reason}")
    return lines


def render_previous_findings(rounds: Sequence[RoundRecord], *, round_index: int) -> str:
    """The Reviewer's own previous findings, or ``""`` when it has none yet.

    A first round, or a later one preceded only by placeholders or
    self-reviews, gets ``""`` and therefore the unchanged prompt.
    """
    if round_index <= 1:
        return ""
    reviewed = independent_review_rounds(rounds)
    if not reviewed:
        return ""
    latest = reviewed[-1]
    review = latest.review
    status = _one_line(review.status, 40) or "unknown"
    reason = str(review.reason or "").strip()
    action = str(review.next_action or "").strip()
    lines = [
        "## Your previous findings",
        f"Your judgment in round {latest.round_index} (`{status}`):",
    ]
    if reason:
        lines.append(_one_line(reason, _REASON_LIMIT))
    if action and action != reason and not reason.startswith(action[:200]):
        lines.append("What you asked for: " + _one_line(action, _ACTION_LIMIT))
    evidence = _items(review.frontier_report, "evidence")
    if evidence:
        lines.append("Evidence you cited:")
        lines.extend(f"- {item}" for item in evidence)
    remaining = _items(review.frontier_report, "remaining_work")
    if remaining:
        lines.append("Open items you named:")
        lines.extend(f"- {item}" for item in remaining)
    lines.extend(_earlier(reviewed[:-1]))
    text = "\n".join(lines)
    return sanitize_model_visible_text(
        redact_secrets_text(text, known_values=known_secret_values())
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
    "independent_review_rounds",
    "own_reviewer_thread",
    "render_previous_findings",
]
