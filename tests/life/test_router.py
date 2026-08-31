from __future__ import annotations

from typing import Any

import pytest

from argus_skill.life.router import (
    _IDENTITY_GUARD,
    build_route_prompt,
    build_simple_prompt,
    classify_route,
)
from argus_skill.roles.prompts.manager import build_quick_reply_prompt


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
    assert "Use SELF unless the requested outcome genuinely needs the team" in p
    assert "code/project modification" in p
    assert "multiple coordinated artifacts" in p
    assert "guided reading/tutoring" in p
    assert "one low-risk summary/note/report artifact" in p


def test_backend_exception_is_safe_default() -> None:
    assert classify_route("x", run_exec=_runner(RuntimeError("boom"))) == "complex"


def test_nonzero_exit_is_safe_default() -> None:
    assert (
        classify_route("x", run_exec=_runner(_FakeResult(message="SELF", exit_code=1))) == "complex"
    )


def test_reads_last_of_agent_messages_when_no_last_message() -> None:
    assert (
        classify_route(
            "hello", run_exec=_runner(_FakeResult(message="", messages=["thinking...", "SELF"]))
        )
        == "simple"
    )


# ---- chat / simple answer prompts ------------------------------------------


def test_build_quick_reply_prompt_names_the_worker_and_guards_identity() -> None:
    out = build_quick_reply_prompt(objective="你好")
    from argus_skill.core.role_config import runner_backend_label

    assert "You are Argus Manager" in out
    assert f"{runner_backend_label()} worker" in out
    assert _IDENTITY_GUARD in out
    assert "identify only as Argus Manager" in out
    assert "我是 Argus Manager。" in out
    assert "I am Argus Manager." in out
    assert "Argus's durable runner" in out
    assert "do not claim inspection" in out
    assert out.endswith("Message:\n你好")


def test_build_quick_reply_prompt_never_points_operator_at_the_backend_cli() -> None:
    """Regression: the Manager must never tell the operator to go run a
    command in "the backend's CLI" to change Argus's own model/backend/
    effort — live-confirmed bad advice ("在 Copilot CLI 里请运行：/model")
    caused by the LLM conflating the transient execution backend with a
    separate tool. The identity guard must always be present so this can't
    silently regress."""
    out = build_quick_reply_prompt(objective="把你的模型换成sonnet 5")
    assert "backend's CLI" in out
    assert "/backend" in out and "/config" in out


def test_build_quick_reply_prompt_includes_identity_when_given() -> None:
    out = build_quick_reply_prompt(objective="who are you", identity_card="I am argus.")
    assert out.index("You are Argus Manager") < out.index("I am argus.\n\n")
    assert "who are you" in out


def test_build_simple_prompt_is_minimal() -> None:
    out = build_simple_prompt(objective="17*23=?")
    assert "17*23" in out
    assert "Argus Manager" in out
    from argus_skill.core.role_config import runner_backend_label

    assert f"{runner_backend_label()} worker" in out
    assert "identify only as Argus Manager" in out
    assert "do not invent extra tasks or artifacts" in out
    assert "ask at most one question" in out
    assert "then wait" in out
    assert "time-by-category cross-slices" in out
    assert "reread the complete draft once" in out


def test_build_simple_prompt_includes_identity_when_given() -> None:
    out = build_simple_prompt(
        objective="are you supervising the daemon?",
        identity_card="Manager operating contract.",
    )
    assert out.index("You are Argus Manager") < out.index("Manager operating contract.\n\n")
    assert "are you supervising the daemon?" in out


def test_build_simple_prompt_never_points_operator_at_the_backend_cli() -> None:
    """Same regression as the quick-reply prompt: this is the path a real free-text
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
    assert out.endswith(status)
    assert "how's it going?" in out
    assert "Argus Manager" in out


def test_build_simple_prompt_orders_stable_contract_before_live_context() -> None:
    status = '## Live mission status\n- item: "demo" (id=abc)'
    out = build_simple_prompt(
        objective="how's it going?",
        identity_card="Manager operating contract.",
        skill_library="## Manager Skills\nStable library index.",
        mission_status=status,
        runtime_context="Runtime fact: warm session.",
    )

    assert out.index("Answer the request yourself") < out.index(
        "Lead with the answer in plain language"
    )
    assert out.index("Lead with the answer in plain language") < out.index(
        "## Manager Skills"
    )
    assert out.index("## Manager Skills") < out.index("Manager operating contract.")
    assert out.index("Task:\nhow's it going?") < out.index(status)


def test_build_simple_prompt_includes_grounding_workspace_when_given() -> None:
    out = build_simple_prompt(
        objective="what frontend does this project use?",
        operator_workspace="/workspace/project",
    )
    assert "Operator launch workspace: /workspace/project" in out
    assert "inspect this workspace with tools" in out
    assert "may modify state or use tools" in out
    assert "Grounding workspace" not in build_simple_prompt(objective="17*23=?")


def test_manager_prompts_include_runtime_context_only_when_given() -> None:
    fact = "Runtime fact: one warm ACP conversation session."
    chat = build_quick_reply_prompt(objective="how are you?", runtime_context=fact)
    simple = build_simple_prompt(objective="status", runtime_context=fact)

    assert fact in chat and fact in simple
    assert fact not in build_quick_reply_prompt(objective="how are you?")
    assert fact not in build_simple_prompt(objective="status")
