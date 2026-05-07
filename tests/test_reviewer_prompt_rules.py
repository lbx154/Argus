"""Snapshot-style tests for the reviewer prompt's decision rules.

These guard against accidental regressions of the "stop early when
evidence is already in front of you" lever and the "blocked is for
needs-user-input only" lever — both have been observed silently breaking
agent throughput when the wording slips. We assert directly on the
prompt text that the model receives.
"""

from __future__ import annotations

from argus_skill.engineer.reviewer import Reviewer


class _StubRunner:
    """Minimal stand-in so we can construct a Reviewer for prompt testing."""

    def run_exec(self, *args, **kwargs):  # pragma: no cover — never called
        raise NotImplementedError


def _build_prompt() -> str:
    rev = Reviewer(_StubRunner())
    return rev._build_prompt(
        objective="implement add()",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="ran pytest, 3 passed",
        main_error=None,
        checks=[],
    )


def test_rule_1a_continue_to_done_lever_present() -> None:
    """Reviewer must stop when verbatim evidence already satisfies request.

    Without this rule the reviewer wastes a full round demanding a re-run
    of commands the agent just executed and pasted back.
    """
    prompt = _build_prompt()
    # The exact phrase that flips it from continue to done.
    assert "Symmetric stop rule" in prompt
    assert "WRONG. Choose `done`" in prompt
    # Anti-pattern hint must call out the re-run trap explicitly.
    assert "wastes rounds" in prompt or "waste" in prompt.lower()


def test_blocked_rule_forbids_fixable_failures() -> None:
    """`blocked` must be reserved for genuine user-input dependencies.

    Failing tests / runtime errors are `continue` not `blocked` — the
    latter terminates the loop early in engineer/runner.py.
    """
    prompt = _build_prompt()
    # Rule 4 names "credentials" as one of the legitimate blocked cases.
    assert "credentials" in prompt
    assert "tests still failing" in prompt
    assert "NOT" in prompt and "blocked" in prompt


def test_evidence_requirement_still_present() -> None:
    """Rule 1 (demand evidence for `done`) must remain — rule 1a softens
    not removes it. Both must coexist."""
    prompt = _build_prompt()
    assert "CONCRETE EVIDENCE" in prompt
    assert "bare assertion" in prompt
