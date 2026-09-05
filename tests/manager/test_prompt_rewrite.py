"""Manager prompt rewrite: parsing, fidelity guards, and fail-soft behaviour."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from argus_skill.manager.prompt_rewrite import (
    parse_rewrite_text,
    rewrite_prompt,
)
from argus_skill.roles.prompts.manager import build_prompt_rewrite_prompt
from argus_skill.webapi.manager_bridge import _rewrite_model_and_effort


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


def test_parses_the_contracted_json_object() -> None:
    parsed = parse_rewrite_text(
        _payload(
            rewritten="Profile the attention kernel on B200 and report the bottleneck.",
            changes=["named the hardware the operator already mentioned"],
            questions=["which attention variant?"],
        )
    )
    assert parsed.rewritten.startswith("Profile the attention kernel")
    assert parsed.changes == ["named the hardware the operator already mentioned"]
    assert parsed.questions == ["which attention variant?"]
    assert parsed.ok


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


def test_prompt_keeps_unagreed_requirements_out_of_the_rewrite() -> None:
    prompt = build_prompt_rewrite_prompt("优化一下 kernel")
    assert "优化一下 kernel" in prompt
    lowered = prompt.lower()
    assert "do not do the work" in lowered
    # The rewrite carries only what the operator asked for...
    assert "belongs in `questions`, not in `rewritten`" in lowered
    assert "must never discover a requirement they did not agree to" in lowered
    assert "same language" in lowered


def test_prompt_invites_the_manager_to_propose_metrics_as_questions() -> None:
    """Proposing is expected; the harness must not gag the Manager's judgment."""
    lowered = build_prompt_rewrite_prompt("优化一下 kernel").lower()
    assert "use your own judgment about what this task actually needs" in lowered
    assert "you should raise it" in lowered
    assert "with your suggested value" in lowered
    assert "proposing is expected; deciding for them is not" in lowered
    # A concrete proposal beats an open-ended prompt.
    assert "prefer a concrete proposal" in lowered
    # The old blanket ban must not creep back in.
    assert "adding new requirements is forbidden" not in lowered
    assert "never invent a metric" not in lowered


def test_prompt_demands_an_actionable_brief_not_a_restatement() -> None:
    lowered = build_prompt_rewrite_prompt("优化一下 kernel").lower()
    assert "a bare restatement of the operator's words is a failed rewrite" in lowered
    assert "count as done" in lowered


def test_prompt_carries_optional_project_context() -> None:
    prompt = build_prompt_rewrite_prompt(
        "make it faster",
        project_context="- working directory: /repo\n- active workflow (vertical): kernel",
    )
    assert "/repo" in prompt
    assert "vertical): kernel" in prompt
    assert "- working directory" not in build_prompt_rewrite_prompt("make it faster")


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


def test_interactive_rewrite_defaults_to_gpt55_high(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_REWRITE_MODEL", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_REWRITE_REASONING_EFFORT", raising=False)

    assert _rewrite_model_and_effort() == ("gpt-5.5", "high")


def test_interactive_rewrite_keeps_operator_overrides(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_REWRITE_MODEL", "custom-rewrite-model")
    monkeypatch.setenv("ARGUS_SKILL_REWRITE_REASONING_EFFORT", "xhigh")

    assert _rewrite_model_and_effort() == ("custom-rewrite-model", "xhigh")
