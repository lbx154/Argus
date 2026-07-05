from __future__ import annotations

from typing import Any

import pytest

from argus_skill.life.router import (
    build_chat_prompt,
    build_classify_prompt,
    build_route_prompt,
    build_simple_prompt,
    classify_is_conversational,
    classify_route,
)


class _FakeResult:
    def __init__(
        self,
        *,
        message: str = "",
        messages: list[str] | None = None,
        exit_code: int = 0,
    ) -> None:
        self.last_agent_message = message
        if messages is not None:
            self.agent_messages = messages
        self.exit_code = exit_code


def _runner(result_or_exc: Any):
    calls: list[str] = []

    def _run_exec(prompt: str) -> Any:
        calls.append(prompt)
        if isinstance(result_or_exc, Exception):
            raise result_or_exc
        return result_or_exc

    _run_exec.calls = calls  # type: ignore[attr-defined]
    return _run_exec


@pytest.mark.parametrize(("answer", "expected"), [
    ("SELF", "simple"), ("self", "simple"), (" SELF ", "simple"),
    ("TEAM", "complex"), ("team", "complex"), ("TEAM.", "complex"),
])
def test_classify_route_two_way(answer: str, expected: str) -> None:
    assert classify_route("x", run_exec=_runner(_FakeResult(message=answer))) == expected


@pytest.mark.parametrize("answer", ["", "maybe", "yes"])
def test_classify_route_unknown_falls_back_to_team(answer: str) -> None:
    assert classify_route("x", run_exec=_runner(_FakeResult(message=answer))) == "complex"


def test_classify_route_empty_is_complex_without_calling_model() -> None:
    run = _runner(_FakeResult(message="SELF"))
    assert classify_route("   ", run_exec=run) == "complex"
    assert run.calls == []  # type: ignore[attr-defined]


def test_route_prompt_has_two_labels() -> None:
    p = build_route_prompt("do a thing", role_skill_block="IGNORED")
    assert "SELF" in p and "TEAM" in p
    assert "IGNORED" not in p
    assert "do a thing" in p
    assert "changes to Argus itself" in p


@pytest.mark.parametrize("answer", ["SELF", "self", " SELF "])
def test_self_answer_is_conversational(answer: str) -> None:
    assert classify_is_conversational("hello", run_exec=_runner(_FakeResult(message=answer))) is True


@pytest.mark.parametrize("answer", ["TEAM", "team", "maybe", ""])
def test_non_self_answer_is_not_conversational(answer: str) -> None:
    assert classify_is_conversational("fix it", run_exec=_runner(_FakeResult(message=answer))) is False


def test_backend_exception_is_team() -> None:
    assert classify_route("x", run_exec=_runner(RuntimeError("boom"))) == "complex"
    assert classify_is_conversational("hi", run_exec=_runner(RuntimeError("boom"))) is False


def test_nonzero_exit_is_team() -> None:
    res = _FakeResult(message="SELF", exit_code=1)
    assert classify_route("x", run_exec=_runner(res)) == "complex"
    assert classify_is_conversational("hi", run_exec=_runner(res)) is False


def test_reads_last_of_agent_messages_when_no_last_message() -> None:
    res = _FakeResult(message="", messages=["thinking...", "SELF"])
    assert classify_route("hello", run_exec=_runner(res)) == "simple"
    assert classify_is_conversational("hello", run_exec=_runner(res)) is True


def test_classify_prompt_is_minimal_and_ignores_role_skill() -> None:
    prompt = build_classify_prompt("你好", role_skill_block="ROLE_SKILL_SENTINEL")
    assert "你好" in prompt
    assert "SELF" in prompt and "TEAM" in prompt
    assert "ROLE_SKILL_SENTINEL" not in prompt


def test_build_chat_prompt_is_minimal() -> None:
    out = build_chat_prompt(objective="你好")
    assert out == "You are Argus Manager. Answer as Argus Manager.\n\nMessage:\n你好"


def test_build_chat_prompt_includes_identity_when_given() -> None:
    out = build_chat_prompt(objective="who are you", identity_card="I am argus.")
    assert out.startswith("I am argus.\n\n")
    assert "who are you" in out


def test_build_simple_prompt_is_minimal_and_ignores_skill() -> None:
    out = build_simple_prompt(objective="17*23=?", skill_block="USE base-arith")
    assert "17*23" in out
    assert "USE base-arith" not in out
    assert "Argus Manager" in out
    assert "Codex worker" in out
    assert "Answer as Argus Manager" in out


def test_build_simple_prompt_omits_mission_status_block_when_empty() -> None:
    # Back-compat: no running mission means the prompt is byte-identical to
    # before live mission status existed.
    with_empty = build_simple_prompt(objective="17*23=?", mission_status="")
    without_arg = build_simple_prompt(objective="17*23=?")
    assert with_empty == without_arg


def test_build_simple_prompt_includes_mission_status_when_given() -> None:
    status = "## Live mission status\n- item: \"demo\" (id=abc)"
    out = build_simple_prompt(objective="how's it going?", mission_status=status)
    assert out.startswith(status + "\n\n")
    assert "how's it going?" in out
    assert "Argus Manager" in out
