"""Tests for the friendly task.completed payload formatter."""
from __future__ import annotations

from dataclasses import dataclass

from argus_skill.daemon.runtime import _format_completion_text


@dataclass
class _FakeOutcome:
    status: str
    round_count: int
    skill_used: str | None
    final_message: str


def test_completion_text_includes_final_answer() -> None:
    outcome = _FakeOutcome(
        status="done",
        round_count=2,
        skill_used="hello",
        final_message="Created hello.py with print('hello world').",
    )
    text = _format_completion_text(outcome)
    assert "status=done" in text
    assert "rounds=2" in text
    assert "skill=hello" in text
    assert "Created hello.py" in text


def test_completion_text_no_progress_emits_friendly_hint() -> None:
    outcome = _FakeOutcome(
        status="no_progress",
        round_count=2,
        skill_used=None,
        final_message="",
    )
    text = _format_completion_text(outcome)
    assert "status=no_progress" in text
    assert "didn't produce a final message" in text
    assert "/run" in text


def test_completion_text_truncates_huge_final_message() -> None:
    long = "x" * 5000
    outcome = _FakeOutcome(
        status="done",
        round_count=1,
        skill_used=None,
        final_message=long,
    )
    text = _format_completion_text(outcome)
    # Header + blank line + 1500-char body + ellipsis ≈ ~1530.
    assert len(text) <= 1600
    assert text.endswith("…")


def test_completion_text_skips_skill_when_none() -> None:
    outcome = _FakeOutcome(
        status="done",
        round_count=1,
        skill_used=None,
        final_message="ok",
    )
    text = _format_completion_text(outcome)
    assert "skill=" not in text
    assert "ok" in text
