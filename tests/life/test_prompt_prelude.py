"""Tests that mission prompts surface the lifetime-agent prelude_context
correctly without polluting the objective."""
from __future__ import annotations

from argus_skill.mission.prompts import (
    continue_main_prompt,
    initial_main_prompt,
)


class _FakeReview:
    reason = "needs more tests"
    next_action = "add a test for X"
    round_summary_markdown = ""


def test_initial_prompt_no_prelude_unchanged() -> None:
    p = initial_main_prompt(objective="do the thing")
    assert "## Objective\ndo the thing" in p
    # No memory header when prelude is empty.
    assert "non-authoritative" not in p.lower()


def test_initial_prompt_renders_prelude_as_separate_section() -> None:
    prelude = (
        "### Memory context (non-authoritative)\n"
        "Identity: terse engineer.\n"
        "Recent: refactored auth."
    )
    p = initial_main_prompt(objective="do the thing", prelude_context=prelude)
    # Objective line is NOT mutated.
    assert "## Objective\ndo the thing" in p
    # Prelude is included verbatim, marked non-authoritative.
    assert "non-authoritative" in p.lower()
    assert "Identity: terse engineer." in p
    # And it appears AFTER the objective heading (so the engineer reads
    # the live task before reading memory).
    obj_idx = p.index("## Objective")
    pre_idx = p.index("Memory context")
    assert obj_idx < pre_idx


def test_initial_prompt_auto_wraps_unmarked_prelude() -> None:
    raw = "Just a sentence with no header."
    p = initial_main_prompt(objective="o", prelude_context=raw)
    assert "non-authoritative" in p.lower()
    assert "Just a sentence with no header." in p


def test_continue_prompt_includes_prelude() -> None:
    p = continue_main_prompt(
        objective="o",
        review=_FakeReview(),
        checks_ok=True,
        prelude_context=(
            "### Memory context (non-authoritative)\nrelevant prior fact"
        ),
    )
    assert "non-authoritative" in p.lower()
    assert "relevant prior fact" in p
    # Reviewer feedback still rendered.
    assert "needs more tests" in p


def test_continue_prompt_no_prelude_does_not_inject_header() -> None:
    p = continue_main_prompt(
        objective="o",
        review=_FakeReview(),
        checks_ok=True,
    )
    assert "non-authoritative" not in p.lower()
