"""Tests for the model-based chat-vs-task classifier.

The classifier replaced a bilingual pile of hand-maintained regexes with
one cheap model call. It is conservative by construction: false negatives
(a chat treated as a task) only cost one needless pipeline run, but false
positives (a real task treated as chat) silently skip the engineer loop
and lose work. So it returns chat ONLY when the model answers exactly
``CHAT`` and returns task on any other answer, parse failure, non-zero
exit, or backend exception.
"""
from __future__ import annotations

from typing import Any

import pytest

from argus_skill.life.router import (
    build_chat_prompt,
    build_classify_prompt,
    classify_is_conversational,
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
    """Build a run_exec callable returning a canned result (or raising)."""

    calls: list[str] = []

    def _run_exec(prompt: str) -> Any:
        calls.append(prompt)
        if isinstance(result_or_exc, Exception):
            raise result_or_exc
        return result_or_exc

    _run_exec.calls = calls  # type: ignore[attr-defined]
    return _run_exec


# ---------- the model says CHAT -> chat -----------------------------------

@pytest.mark.parametrize("answer", ["CHAT", "chat", " CHAT ", "CHAT.", "Chat\n"])
def test_clear_chat_answer_is_conversational(answer: str) -> None:
    assert classify_is_conversational("hello", run_exec=_runner(_FakeResult(message=answer))) is True


# ---------- the model says TASK (or anything else) -> task ----------------

@pytest.mark.parametrize(
    "answer",
    ["TASK", "task", "TASK — imperative", "I think this is a task", "CHATTER", "", "maybe"],
)
def test_non_chat_answer_is_task(answer: str) -> None:
    assert classify_is_conversational("fix it", run_exec=_runner(_FakeResult(message=answer))) is False


# ---------- false-positive safety: terse real tasks must NOT be chat ------
# These are the dangerous cases the old regex guarded; the classifier must
# be biased so that a model returning anything but a clean CHAT routes to
# the pipeline. We assert the contract: a TASK answer -> not conversational.

@pytest.mark.parametrize(
    "msg",
    ["fix it", "continue", "继续", "run it", "do the next step", "make it work",
     "yes, proceed", "ok now fix", "try again", "接着做"],
)
def test_terse_imperatives_route_to_pipeline_when_model_says_task(msg: str) -> None:
    assert classify_is_conversational(msg, run_exec=_runner(_FakeResult(message="TASK"))) is False


# ---------- robustness: errors / exits / empty input all -> task ----------

def test_backend_exception_is_task() -> None:
    assert classify_is_conversational("hi", run_exec=_runner(RuntimeError("boom"))) is False


def test_nonzero_exit_is_task_even_if_message_says_chat() -> None:
    res = _FakeResult(message="CHAT", exit_code=1)
    assert classify_is_conversational("hi", run_exec=_runner(res)) is False


def test_empty_input_is_task_without_calling_model() -> None:
    run = _runner(_FakeResult(message="CHAT"))
    assert classify_is_conversational("   ", run_exec=run) is False
    assert run.calls == []  # type: ignore[attr-defined]


def test_reads_last_of_agent_messages_when_no_last_message() -> None:
    res = _FakeResult(message="", messages=["thinking…", "CHAT"])
    assert classify_is_conversational("hello", run_exec=_runner(res)) is True


def test_classify_prompt_contains_message_and_labels() -> None:
    prompt = build_classify_prompt("你好")
    assert "你好" in prompt
    assert "CHAT" in prompt and "TASK" in prompt


# ---------- role_skill_block injection (Manager wires its role skill in) ----

def test_classify_prompt_byte_identical_when_block_empty() -> None:
    # The default empty role_skill_block must produce a prompt byte-for-byte
    # identical to the legacy one-arg call (full back-compat for every caller
    # that does not pass a block — and for a Manager with no skill_store).
    assert build_classify_prompt("你好", "") == build_classify_prompt("你好")


def test_classify_prompt_prepends_non_empty_block() -> None:
    block = "ROLE_SKILL_SENTINEL\n\n"
    prompt = build_classify_prompt("你好", block)
    # The block is prepended verbatim, ahead of the original instructions.
    assert prompt.startswith(block)
    assert "ROLE_SKILL_SENTINEL" in prompt
    # The rest of the prompt is exactly the legacy prompt appended after it.
    assert prompt == block + build_classify_prompt("你好")


def test_classify_forwards_role_skill_block_to_prompt() -> None:
    # classify_is_conversational threads role_skill_block through to the prompt
    # the model actually receives.
    run = _runner(_FakeResult(message="TASK"))
    classify_is_conversational(
        "fix it", run_exec=run, role_skill_block="ROLE_SKILL_SENTINEL\n\n"
    )
    assert run.calls and "ROLE_SKILL_SENTINEL" in run.calls[0]  # type: ignore[attr-defined]


def test_classify_default_block_is_byte_identical_in_call() -> None:
    # Not passing role_skill_block sends the legacy prompt unchanged.
    run_default = _runner(_FakeResult(message="TASK"))
    classify_is_conversational("fix it", run_exec=run_default)
    assert run_default.calls == [build_classify_prompt("fix it")]  # type: ignore[attr-defined]


# ---------- chat prompt builder (unchanged surface) -----------------------

def test_build_chat_prompt_contains_user_message() -> None:
    out = build_chat_prompt(objective="你好")
    assert "你好" in out
    assert "CHAT mode" in out
    assert "Verification" not in out or "OFF" in out
    assert "## Required output" not in out


def test_build_chat_prompt_includes_identity_when_given() -> None:
    out = build_chat_prompt(objective="who are you", identity_card="I am argus.")
    assert "I am argus." in out
    assert "who are you" in out


def test_build_chat_prompt_omits_identity_when_blank() -> None:
    out = build_chat_prompt(objective="hi", identity_card="   ")
    assert "Identity context" not in out
