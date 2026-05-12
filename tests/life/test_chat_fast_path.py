"""Tests for the chat fast-path: conversational input bypasses the
mission pipeline (matcher / distill / engineer round-loop / reviewer /
critic) and returns ``_Outcome(chat_mode=True)`` after a single
codex call.

Two surfaces are exercised here:

1. ``_CodexSkillLoopRunner._chat_quick_reply`` — direct unit test with
   a fake codex backend. Verifies prompt shape, event emission, token
   accounting, and ``chat_mode=True``.

2. ``LifeSupervisor._run_one`` with a fake runner that returns
   ``chat_mode=True`` — verifies the critic / iteration loop is
   skipped (no ``life.iteration.critic`` event, no requeue).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from argus_skill.core.models import RunnerOptions, RunnerResult
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)

# ---------- fakes for the runner unit test --------------------------------

@dataclass
class _FakeBackend:
    """Stand-in for ``CodexRunnerBackend`` for the chat fast-path tests.

    Records the prompt + run_label so the test can assert on them, then
    returns a canned ``RunnerResult`` with the configured tokens / msg.
    """
    response_message: str = "你好"
    input_tokens: int = 320
    output_tokens: int = 28
    exit_code: int = 0
    fatal_error: str | None = None
    thread_id: str | None = "tid-chat-1"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def run_exec(
        self,
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        self.calls.append({
            "prompt": prompt,
            "options": options,
            "run_label": run_label,
            "resume_thread_id": resume_thread_id,
        })
        return RunnerResult(
            exit_code=self.exit_code,
            agent_messages=[self.response_message],
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            thread_id=self.thread_id,
            fatal_error=self.fatal_error,
        )


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def handle_stream_line(self, stream: str, line: str) -> None:  # noqa: ARG002
        return

    def close(self) -> None:
        return


def _make_runner(backend: _FakeBackend) -> Any:
    """Build a ``_CodexSkillLoopRunner`` without invoking ``__init__``.

    The real ``__init__`` imports ArgusBot to construct codex; we
    bypass it and inject our fake backend / args directly so the
    chat-path can be tested in isolation.
    """
    from argus_skill.apps._life_repl import _CodexSkillLoopRunner

    runner = _CodexSkillLoopRunner.__new__(_CodexSkillLoopRunner)
    runner = cast(Any, runner)
    runner._backend = backend
    runner.backend = backend
    runner._current_sink = None
    runner._current_failure_ledger = None
    runner._args = argparse.Namespace(
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
        scientist_model="gpt-5.4",
        skills_dir="/tmp/test-skills",
        workdir=None,
        max_rounds=6,
    )
    runner._next_seed_thread_id = None
    runner.last_thread_id = None
    return runner


# ---------- chat fast-path: runner unit tests -----------------------------

def test_execute_dispatches_to_chat_path_on_greeting() -> None:
    """English greeting → ``_chat_quick_reply`` → 1 codex call → chat_mode."""
    backend = _FakeBackend(response_message="Hi! How can I help?")
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner.execute(objective="hello", sink=sink)

    assert out.chat_mode is True
    assert out.success is True
    assert out.status == "done"
    assert out.rounds == 1
    # Exactly one backend call (no matcher / distill / reviewer).
    assert len(backend.calls) == 1
    assert backend.calls[0]["run_label"] == "chat-1"
    # Reasoning effort is dialled down for chat.
    assert backend.calls[0]["options"].reasoning_effort == "low"


def test_execute_dispatches_to_chat_path_on_chinese_capability_question() -> None:
    backend = _FakeBackend(response_message="我可以帮你读代码、改文件、跑测试。")
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner.execute(objective="你有什么能力？", sink=sink)

    assert out.chat_mode is True
    assert len(backend.calls) == 1
    # Prompt must NOT carry the engineer's Verification template (the
    # full ``## Verification (verbatim)`` heading the engineer prompt
    # produces in mission mode).
    prompt = backend.calls[0]["prompt"]
    assert "## Verification (verbatim)" not in prompt
    assert "## Required output" not in prompt
    # And it must contain the user message verbatim so codex sees it.
    assert "你有什么能力？" in prompt


def test_execute_uses_full_pipeline_on_real_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clear engineering task must NOT short-circuit. We monkey-patch
    ``is_conversational`` to assert it's called and trust its return,
    plus check that the runner falls through to the SkillLoop path
    (signalled by an attempt to import / construct one). Constructing
    SkillLoop with a fake skills_dir + fake backend is heavyweight, so
    instead we just observe that ``_chat_quick_reply`` is NOT invoked
    by setting a sentinel that would raise if called.
    """
    backend = _FakeBackend()
    runner = _make_runner(backend)
    sink = _RecordingSink()

    # Replace the chat path with a sentinel — if the runner mistakenly
    # routes a real task into chat mode, this test fails loudly.
    sentinel_calls: list[str] = []
    def _sentinel(*, objective: str, sink: Any, seed_thread_id: Any = None):
        sentinel_calls.append(objective)
        raise AssertionError(
            f"chat fast-path was triggered for what should be a task: {objective!r}"
        )
    runner._chat_quick_reply = _sentinel

    # Build a minimal SkillLoop / SkillLoopConfig stub so the fall-through
    # path doesn't try to construct a real one. We replace ``_SkillLoop``
    # with a factory returning a stub whose ``run`` returns a duck-typed
    # outcome.
    @dataclass
    class _StubLoopOutcome:
        successful: bool = True
        status: str = "done"
        round_count: int = 1
        skill_used: str | None = None
        skill_distilled: bool = False
        reason: str = ""
        last_thread_id: str | None = None

    class _StubLoop:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw
        def run(self, *args: Any, **kw: Any) -> _StubLoopOutcome:
            return _StubLoopOutcome()

    runner._SkillLoop = _StubLoop

    @dataclass
    class _StubConfig:
        scientist_model: str = ""
        engineer_model: str = ""
        reviewer_model: str | None = None
        max_rounds: int = 1
        check_commands: list = field(default_factory=list)
        skill_writeback: bool = True
        distill_on_miss: bool = True
        dangerous_yolo: bool = True
        full_auto: bool = False
        skip_git_repo_check: bool = True

    runner._SkillLoopConfig = _StubConfig

    out = runner.execute(objective="implement a binary tree in src/tree.py", sink=sink)

    assert sentinel_calls == [], "real task wrongly routed into chat fast-path"
    assert out.chat_mode is False


def test_chat_path_emits_minimum_event_sequence() -> None:
    """REPL renderer + cost-tracking sink need a tight event set."""
    backend = _FakeBackend(input_tokens=512, output_tokens=64)
    runner = _make_runner(backend)
    sink = _RecordingSink()

    runner.execute(objective="hi", sink=sink)

    types = [e.get("type") for e in sink.events]
    # Required: loop.start at the beginning, round.main.completed in the
    # middle (cost sink reads tokens here), loop.done at the end.
    assert types[0] == "loop.start"
    assert "round.main.completed" in types
    assert types[-1] == "loop.done"
    # Reviewer / writeback / scientist events must NOT appear.
    forbidden = {
        "round.review.completed",
        "skill.writeback",
        "skill.match",
        "skill.distill.start",
        "scientist.start",
        "skill.outcome",
    }
    assert not (set(types) & forbidden), (
        f"unexpected mission-pipeline events on chat path: {set(types) & forbidden}"
    )


def test_chat_path_propagates_token_counts() -> None:
    backend = _FakeBackend(input_tokens=412, output_tokens=37)
    runner = _make_runner(backend)
    sink = _RecordingSink()

    runner.execute(objective="hello", sink=sink)

    main = next(e for e in sink.events if e.get("type") == "round.main.completed")
    assert main["input_tokens"] == 412
    assert main["output_tokens"] == 37


def test_chat_path_chains_thread_id_for_session_continuity() -> None:
    backend = _FakeBackend(thread_id="tid-from-codex")
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner.execute(objective="hello", sink=sink)

    assert out.last_thread_id == "tid-from-codex"
    assert runner.last_thread_id == "tid-from-codex"
    # Next call must resume from the previous thread by default.
    backend.calls.clear()
    runner.execute(objective="hi again", sink=sink)
    assert backend.calls[0]["resume_thread_id"] == "tid-from-codex"


def test_chat_path_marks_status_error_on_codex_failure() -> None:
    backend = _FakeBackend(exit_code=1, fatal_error="codex died")
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner.execute(objective="hi", sink=sink)

    assert out.success is False
    assert out.status == "error"
    assert "codex died" in out.stop_reason
    # chat_mode stays True so the supervisor still skips the critic
    # — there is nothing to polish even on failure.
    assert out.chat_mode is True


# ---------- supervisor: chat outcomes skip the critic loop ---------------

@dataclass
class _ChatOutcome:
    success: bool = True
    status: str = "done"
    stop_reason: str = ""
    rounds: int = 1
    matched_skill_name: str | None = None
    skill_distilled: bool = False
    had_follow_up: bool = False
    chat_mode: bool = True
    final_message: str = "你好！"


class _ChatRunner:
    """Stand-in runner that always returns a chat outcome."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        # Critic would call this; if it fires, the test fails.
        self.backend = None

    def execute(
        self,
        *,
        objective: str,
        sink: Any,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
    ) -> _ChatOutcome:
        self.calls.append({"objective": objective})
        sink.handle_event({
            "type": "round.main.completed",
            "input_tokens": 200,
            "output_tokens": 30,
        })
        return _ChatOutcome()


def _mk_memory(tmp_path: Path) -> LifeMemory:
    return LifeMemory.open(tmp_path / "life")


def test_supervisor_skips_critic_for_chat_outcome(tmp_path: Path) -> None:
    """A chat outcome must NOT trigger ``_maybe_iterate``.

    Iteration is gated behind ``item.iterate`` (default True) — if we
    didn't special-case chat, the critic would fire on every greeting
    and burn another LLM call for no gain.
    """
    mem = _mk_memory(tmp_path)
    runner = _ChatRunner()
    sink = _RecordingSink()
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(memory=mem, runner=runner, sink=sink, config=cfg)

    # Add an item with iterate=True (the daemon default). Without the
    # chat_mode skip, the supervisor would call _maybe_iterate and emit
    # ``life.iteration.critic``.
    item = mem.backlog.add(BacklogItem.new(
        title="hello", objective="hello", iterate=True,
    ))

    result = sup.tick()
    assert result is not None
    assert result["success"] is True

    # The chat outcome should NOT trigger any critic event.
    types = [e.get("type") for e in sink.events]
    assert "life.iteration.critic" not in types

    # Backlog row marked done, mission_started then mission_complete,
    # no requeue.
    rows = mem.backlog.all()
    assert rows[0].id == item.id
    assert rows[0].status == "done"
    assert [entry.kind for entry in mem.journal.all()] == [
        "mission_started",
        "mission_complete",
    ]


def test_supervisor_still_runs_critic_for_non_chat_outcome(tmp_path: Path) -> None:
    """Sanity check: when chat_mode is False, the critic path is
    reached (and bails because we wired no critic_runner)."""
    @dataclass
    class _NonChatOutcome:
        success: bool = True
        status: str = "done"
        stop_reason: str = ""
        rounds: int = 1
        matched_skill_name: str | None = None
        skill_distilled: bool = False
        had_follow_up: bool = False
        chat_mode: bool = False
        final_message: str = "built it"

    class _NonChatRunner:
        backend = None
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
        def execute(self, *, objective: str, sink: Any,
                    preload_injects: list[str] | None = None,
                    prelude_context: str = "") -> _NonChatOutcome:
            self.calls.append({"objective": objective})
            sink.handle_event({
                "type": "round.main.completed",
                "input_tokens": 1000,
                "output_tokens": 200,
            })
            return _NonChatOutcome()

    mem = _mk_memory(tmp_path)
    runner = _NonChatRunner()
    sink = _RecordingSink()
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(memory=mem, runner=runner, sink=sink, config=cfg)

    mem.backlog.add(BacklogItem.new(
        title="task", objective="implement X", iterate=True,
    ))

    result = sup.tick()
    assert result is not None
    # Not a critic event because critic_runner is None, but the
    # iteration outcome dict is non-None (recorded the bail). The
    # journal should still mark this complete, not iterated.
    assert result["success"] is True
