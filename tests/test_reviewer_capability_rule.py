"""The Reviewer's evidence rule: it has the Engineer's tools, so it reruns.

Every Reviewer runs with the Engineer's access, so one rule fits all of them:
rerun the decisive check, and fall back to recorded runs and its own reading
only when the sandbox blocks the rerun. The Engineer's cited claim alone is
never evidence.
"""
from __future__ import annotations

import pytest

from argus.reviewer import Reviewer
from tests.role_rules import lists, rule

_RULE = rule("reviewer", "checking-the-work/SKILL.md")


class _Runner:
    def __init__(self, backend: str) -> None:
        self.backend = backend


@pytest.mark.parametrize("backend", ["codex", "copilot", "claude", "pi", "qoder", "memory", ""])
def test_every_reviewer_reruns_the_check_itself(backend: str) -> None:
    prompt = Reviewer(runner=_Runner(backend), skill_store=None)._build_static_preamble(
        objective="Make the parser accept the documented grammar.",
        operator_messages=[], planner_review_instruction="", round_index=1,
        session_id=None, main_summary="All tests pass.", main_error=None,
        prior_checkpoint={},
    )
    assert lists(prompt, "checking-the-work/SKILL.md")
    assert "never ask for an execution tool" not in prompt


def test_the_rule_refuses_the_engineers_word_alone_and_falls_back_when_blocked() -> None:
    assert "the Engineer's cited claim alone is not evidence" in _RULE
    assert "rerun the decisive check yourself" in _RULE
    assert "If the sandbox blocks the rerun (write or network denied)" in _RULE
    assert "judge from the recorded runs and your own reading" in _RULE


def test_the_masked_display_sentence_follows_the_rule() -> None:
    _evidence, _, display = _RULE.partition("`******` or `<REDACTED:…>` is display masking")
    assert "proves neither a defect nor a fix" in display
    assert "settle it with a rerunnable assertion" in display
    assert "a placeholder value" in display
