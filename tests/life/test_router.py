from __future__ import annotations

from typing import Any

import pytest

from argus_skill.life.router import (
    _IDENTITY_GUARD,
    build_chat_prompt,
    build_classify_prompt,
    build_persistence_prompt,
    build_route_prompt,
    build_simple_prompt,
    classify_is_conversational,
    classify_needs_persistence,
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


# ---- classify_route: SELF (simple) vs TEAM (complex) -----------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("SELF", "simple"),
        ("self", "simple"),
        (" SELF ", "simple"),
        ("TEAM", "complex"),
        ("team", "complex"),
        ("TEAM.", "complex"),
    ],
)
def test_classify_route_two_way(answer: str, expected: str) -> None:
    assert classify_route("x", run_exec=_runner(_FakeResult(message=answer))) == expected


@pytest.mark.parametrize("answer", ["", "maybe", "yes"])
def test_classify_route_unknown_falls_back_to_team(answer: str) -> None:
    assert classify_route("x", run_exec=_runner(_FakeResult(message=answer))) == "complex"


def test_classify_route_empty_is_complex_without_calling_model() -> None:
    run = _runner(_FakeResult(message="SELF"))
    assert classify_route("   ", run_exec=run) == "complex"
    assert run.calls == []  # type: ignore[attr-defined]


def test_route_prompt_has_two_labels_and_safe_default() -> None:
    p = build_route_prompt("do a thing")
    assert "SELF" in p and "TEAM" in p
    assert "do a thing" in p
    assert "Argus itself" in p
    assert "When in doubt, answer TEAM" in p


# ---- classify_is_conversational: CHAT (True) vs TASK (False) ----------------


@pytest.mark.parametrize("answer", ["CHAT", "chat", " CHAT "])
def test_chat_answer_is_conversational(answer: str) -> None:
    assert (
        classify_is_conversational("hello", run_exec=_runner(_FakeResult(message=answer))) is True
    )


@pytest.mark.parametrize("answer", ["TASK", "task", "SELF", "maybe", ""])
def test_non_chat_answer_is_not_conversational(answer: str) -> None:
    # Anything that is not exactly CHAT resolves to TASK — a real task is never
    # silently answered as chat. (SELF, the old route token, is NOT conversational.)
    assert (
        classify_is_conversational("fix it", run_exec=_runner(_FakeResult(message=answer))) is False
    )


def test_backend_exception_is_safe_default() -> None:
    assert classify_route("x", run_exec=_runner(RuntimeError("boom"))) == "complex"
    assert classify_is_conversational("hi", run_exec=_runner(RuntimeError("boom"))) is False


def test_nonzero_exit_is_safe_default() -> None:
    assert (
        classify_route("x", run_exec=_runner(_FakeResult(message="SELF", exit_code=1))) == "complex"
    )
    assert (
        classify_is_conversational("hi", run_exec=_runner(_FakeResult(message="CHAT", exit_code=1)))
        is False
    )


def test_reads_last_of_agent_messages_when_no_last_message() -> None:
    assert (
        classify_route(
            "hello", run_exec=_runner(_FakeResult(message="", messages=["thinking...", "SELF"]))
        )
        == "simple"
    )
    assert (
        classify_is_conversational(
            "hello", run_exec=_runner(_FakeResult(message="", messages=["thinking...", "CHAT"]))
        )
        is True
    )


def test_classify_prompt_asks_chat_or_task() -> None:
    prompt = build_classify_prompt("你好")
    assert "你好" in prompt
    assert "CHAT" in prompt and "TASK" in prompt
    assert "When in doubt, answer TASK" in prompt


# ---- chat / simple answer prompts ------------------------------------------


def test_build_chat_prompt_names_the_worker_and_guards_identity() -> None:
    out = build_chat_prompt(objective="你好")
    from argus_skill.cli.roles_status import runner_backend_label

    assert "You are Argus Manager" in out
    assert f"{runner_backend_label()} worker" in out
    assert _IDENTITY_GUARD in out
    assert out.endswith("Message:\n你好")


def test_build_chat_prompt_never_points_operator_at_the_backend_cli() -> None:
    """Regression: the Manager must never tell the operator to go run a
    command in "the backend's CLI" to change Argus's own model/backend/
    effort — live-confirmed bad advice ("在 Copilot CLI 里请运行：/model")
    caused by the LLM conflating the transient execution backend with a
    separate tool. The identity guard must always be present so this can't
    silently regress."""
    out = build_chat_prompt(objective="把你的模型换成sonnet 5")
    assert "backend's CLI" in out
    assert "/backend" in out and "/config" in out


def test_build_chat_prompt_includes_identity_when_given() -> None:
    out = build_chat_prompt(objective="who are you", identity_card="I am argus.")
    assert out.startswith("I am argus.\n\n")
    assert "who are you" in out


def test_build_simple_prompt_is_minimal() -> None:
    out = build_simple_prompt(objective="17*23=?")
    assert "17*23" in out
    assert "Argus Manager" in out
    from argus_skill.cli.roles_status import runner_backend_label

    assert f"{runner_backend_label()} worker" in out
    assert "Answer as Argus Manager" in out


def test_build_simple_prompt_never_points_operator_at_the_backend_cli() -> None:
    """Same regression as build_chat_prompt: this is the path a real free-text
    config request (e.g. "把你的模型换成sonnet 5") actually takes once routed
    SELF/simple — confirmed live to produce "在 Copilot CLI 里请运行：/model"
    without this guard."""
    out = build_simple_prompt(objective="把你的模型换成sonnet 5")
    assert "backend's CLI" in out
    assert "/backend" in out and "/config" in out


def test_build_simple_prompt_omits_mission_status_block_when_empty() -> None:
    with_empty = build_simple_prompt(objective="17*23=?", mission_status="")
    without_arg = build_simple_prompt(objective="17*23=?")
    assert with_empty == without_arg


def test_build_simple_prompt_includes_mission_status_when_given() -> None:
    status = '## Live mission status\n- item: "demo" (id=abc)'
    out = build_simple_prompt(objective="how's it going?", mission_status=status)
    assert out.startswith(status + "\n\n")
    assert "how's it going?" in out
    assert "Argus Manager" in out


def test_manager_prompts_include_runtime_context_only_when_given() -> None:
    fact = "Runtime fact: one warm ACP conversation session."
    chat = build_chat_prompt(objective="how are you?", runtime_context=fact)
    simple = build_simple_prompt(objective="status", runtime_context=fact)

    assert fact in chat and fact in simple
    assert fact not in build_chat_prompt(objective="how are you?")
    assert fact not in build_simple_prompt(objective="status")


# ---- classify_needs_persistence: BOUNDED vs STANDING -----------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("STANDING", True),
        ("standing", True),
        (" STANDING ", True),
        ("CONTINUOUS", True),
        ("PERSIST", True),
        ("PERSISTENT", True),
        ("BOUNDED", False),
        ("bounded", False),
        ("BOUNDED.", False),
    ],
)
def test_classify_needs_persistence_two_way(answer: str, expected: bool) -> None:
    assert (
        classify_needs_persistence("x", run_exec=_runner(_FakeResult(message=answer))) is expected
    )


@pytest.mark.parametrize("answer", ["", "maybe", "yes"])
def test_classify_needs_persistence_unknown_falls_back_to_bounded(answer: str) -> None:
    assert classify_needs_persistence("x", run_exec=_runner(_FakeResult(message=answer))) is False


def test_classify_needs_persistence_empty_is_bounded_without_calling_model() -> None:
    run = _runner(_FakeResult(message="STANDING"))
    assert classify_needs_persistence("   ", run_exec=run) is False
    assert run.calls == []  # type: ignore[attr-defined]


def test_classify_needs_persistence_backend_exception_is_bounded() -> None:
    assert classify_needs_persistence("x", run_exec=_runner(RuntimeError("boom"))) is False


def test_classify_needs_persistence_nonzero_exit_is_bounded() -> None:
    res = _FakeResult(message="STANDING", exit_code=1)
    assert classify_needs_persistence("x", run_exec=_runner(res)) is False


def test_persistence_prompt_has_two_labels_and_safe_default_hint() -> None:
    p = build_persistence_prompt("optimize all the kernels")
    assert "BOUNDED" in p and "STANDING" in p
    assert "optimize all the kernels" in p
    assert "When in doubt, answer BOUNDED" in p
