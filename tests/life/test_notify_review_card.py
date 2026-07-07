"""Tests for ``TelegramStreamReporter``'s review-verdict card formatting.

Point 11 of the 11-point CLI directive: the reviewer's ``operator_question``
(reviewer_schema.json) must keep being maintained "in detail" — an operator
running the daemon headless (no interactive REPL attached) has NO other
channel to learn a mission blocked waiting on them specifically. This file
guards the fix: ``_format_review_card_locked`` must surface
``operator_question`` distinctly from the routine ``reason``/``next_action``
narration, and must not regress plain review cards that carry no question.
"""

from __future__ import annotations

from typing import Any

from argus_skill.life.notify import TelegramStreamReporter


def _reporter() -> TelegramStreamReporter:
    """A reporter instance with no real Telegram credentials — safe to build
    directly in-process since ``__init__`` does no network I/O and
    ``_format_review_card_locked`` is a pure formatting method."""
    return TelegramStreamReporter()


def _review_event(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": "round.review.completed",
        "round_index": 2,
        "status": "blocked",
        "reason": "ambiguous which precision to standardize on",
        "next_action": "",
        "operator_question": "",
    }
    base.update(overrides)
    return base


def test_review_card_surfaces_operator_question_distinctly() -> None:
    reporter = _reporter()
    event = _review_event(
        operator_question="fp16 精度损失可接受吗，还是必须 fp32？",
    )
    card = reporter._format_review_card_locked(event)
    assert "需要你回复" in card
    assert "fp16 精度损失可接受吗，还是必须 fp32？" in card
    assert "❓" in card
    # The routine reason line must still be present alongside it.
    assert "ambiguous which precision to standardize on" in card


def test_review_card_has_no_question_marker_when_field_empty() -> None:
    reporter = _reporter()
    event = _review_event(status="done", reason="", operator_question="")
    card = reporter._format_review_card_locked(event)
    assert "需要你回复" not in card
    assert "❓" not in card


def test_review_card_escapes_html_in_operator_question() -> None:
    """The card is sent as Telegram HTML — an operator_question containing
    "<"/">"/"&" must not corrupt the message markup."""
    reporter = _reporter()
    event = _review_event(operator_question="use <script> or & instead?")
    card = reporter._format_review_card_locked(event)
    assert "<script>" not in card
    assert "&lt;script&gt;" in card


def test_review_card_truncates_very_long_operator_question() -> None:
    reporter = _reporter()
    event = _review_event(operator_question="x" * 1000)
    card = reporter._format_review_card_locked(event)
    # Truncated well below the raw 1000 chars (schema caps this at 500
    # anyway, but the card formatter has its own independent display cap).
    assert card.count("x") < 1000
