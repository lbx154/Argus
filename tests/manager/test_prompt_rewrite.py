"""Manager prompt rewrite: parsing, fidelity guards, and fail-soft behaviour."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from argus.manager.prompt_rewrite import (
    parse_rewrite_text,
    rewrite_prompt,
)


class _Backend:
    """Minimal run_exec-shaped stub (no live model)."""

    def __init__(self, message: str = "", exit_code: int = 0, raises: bool = False) -> None:
        self.message = message
        self.exit_code = exit_code
        self.raises = raises
        self.prompts: list[str] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):  # noqa: ANN001
        self.prompts.append(prompt)
        if self.raises:
            raise RuntimeError("backend exploded")
        return SimpleNamespace(
            exit_code=self.exit_code,
            last_agent_message=self.message,
            agent_messages=[self.message],
        )


def _payload(**kwargs) -> str:
    return json.dumps(kwargs, ensure_ascii=False)


# --- parsing ---------------------------------------------------------------


def test_parses_json_wrapped_in_a_code_fence() -> None:
    parsed = parse_rewrite_text(
        "```json\n" + _payload(rewritten="Fix the flaky test in tests/test_a.py.") + "\n```"
    )
    assert parsed.rewritten == "Fix the flaky test in tests/test_a.py."


def test_plain_text_reply_is_used_rather_than_discarded() -> None:
    parsed = parse_rewrite_text("Rewrite the loader so it streams rows.")
    assert parsed.rewritten == "Rewrite the loader so it streams rows."
    assert parsed.changes == []


def test_empty_or_unusable_reply_yields_no_rewrite() -> None:
    assert parse_rewrite_text("").rewritten == ""
    assert parse_rewrite_text("   ").rewritten == ""
    assert parse_rewrite_text('{"unrelated": 1}').rewritten == ""


def test_advisory_lists_pass_through_in_full() -> None:
    parsed = parse_rewrite_text(
        _payload(
            rewritten="do the thing",
            questions=[f"q{i}" for i in range(20)],
        )
    )
    assert len(parsed.questions) == 20


# --- the prompt contract ---------------------------------------------------


# --- rewrite_prompt end to end (stubbed backend) ---------------------------


def test_rewrite_prompt_returns_the_parsed_rewrite() -> None:
    backend = _Backend(_payload(rewritten="Make the CSV loader stream rows."))
    result = rewrite_prompt(backend, "make the loader better")
    assert result.ok
    assert result.rewritten == "Make the CSV loader stream rows."
    assert result.original == "make the loader better"
    assert result.error == ""
    assert "make the loader better" in backend.prompts[0]


@pytest.mark.parametrize(
    ("backend", "draft", "expected"),
    [
        (None, "x", "no runner backend"),
        (_Backend(_payload(rewritten="y"), exit_code=2), "x", "exited non-zero"),
        (_Backend(""), "x", "empty or unparseable"),
        (_Backend(raises=True), "x", "backend error"),
    ],
)
def test_failures_are_explicit_and_never_fabricate(backend, draft, expected) -> None:
    result = rewrite_prompt(backend, draft)
    assert not result.ok
    assert expected in result.error
    # The operator's text must survive a failed rewrite untouched.
    assert result.original == draft
    assert result.rewritten == ""


def test_empty_draft_is_rejected_without_calling_the_model() -> None:
    backend = _Backend(_payload(rewritten="something"))
    result = rewrite_prompt(backend, "   ")
    assert result.error == "nothing to rewrite"
    assert backend.prompts == []
