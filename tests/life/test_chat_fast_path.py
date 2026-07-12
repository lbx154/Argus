"""Tests for the chat fast-path: conversational input bypasses the
mission pipeline (matcher / distill / engineer round-loop / reviewer /
critic) and returns ``_Outcome(chat_mode=True)`` after a single
codex call.

Two surfaces are exercised here:

1. ``_SkillLoopRunner._simple_quick_reply`` — direct unit test with
   a fake backend. Verifies prompt shape, event emission, token
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
    """Stand-in for ``AgentCliBackend`` for the chat fast-path tests.

    Records the prompt + run_label so the test can assert on them, then
    returns a canned ``RunnerResult`` with the configured tokens / msg.
    """
    response_message: str = "你好"
    input_tokens: int = 320
    output_tokens: int = 28
    exit_code: int = 0
    fatal_error: str | None = None
    thread_id: str | None = "tid-chat-1"
    classify_answer: str = "SELF"
    calls: list[dict[str, Any]] = field(default_factory=list)
    classify_calls: list[dict[str, Any]] = field(default_factory=list)

    def run_exec(
        self,
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        if run_label in ("router-classify", "router-classify-persistence"):
            # The chat/task classifier call (and the sibling BOUNDED/STANDING
            # persistence classifier). Kept in a SEPARATE list so the
            # existing assertions about chat/pipeline ``calls`` still hold.
            # Always exit 0 (the classifier itself succeeds; only the chat
            # reply may fail) and answer with the configured verdict.
            self.classify_calls.append({
                "prompt": prompt,
                "options": options,
                "run_label": run_label,
                "resume_thread_id": resume_thread_id,
            })
            return RunnerResult(
                exit_code=0,
                agent_messages=[self.classify_answer],
                input_tokens=1,
                output_tokens=1,
                thread_id=None,
            )
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
    """Build a ``_SkillLoopRunner`` without invoking ``__init__``.

    The real ``__init__`` imports ArgusBot to construct codex; we
    bypass it and inject our fake backend / args directly so the
    chat-path can be tested in isolation.
    """
    from argus_skill.apps._runtime import _SkillLoopRunner

    runner = _SkillLoopRunner.__new__(_SkillLoopRunner)
    runner = cast(Any, runner)
    runner._backend = backend
    runner.backend = backend
    runner.manager_backend = backend
    runner._current_sink = None
    runner._current_failure_ledger = None
    runner._args = argparse.Namespace(
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4",
        skills_dir="/tmp/test-skills",
        workdir=None,
        max_rounds=6,
    )
    runner._next_seed_thread_id = None
    runner.last_thread_id = None
    # Operator-REPL free text is chat-eligible in these unit tests.
    runner._allow_chat_fast_path = True
    # The runner now holds the single Manager instance; ``_maybe_chat_outcome``
    # routes the chat-vs-task classification through it. Wire it to the fake
    # backend so the real classifier path is exercised end-to-end.
    from argus_skill.manager import Manager

    runner.manager = Manager(project_root=Path.cwd(), runner=backend)
    return runner


# ---------- Manager SELF fast-path: runner unit tests ----------------------

def test_execute_dispatches_to_manager_self_path_on_greeting() -> None:
    """English greeting → one Manager turn, no team pipeline."""
    backend = _FakeBackend(response_message="Hi! How can I help?")
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner.execute(objective="hello", sink=sink)

    assert out.chat_mode is False
    assert out.success is True
    assert out.status == "done"
    assert out.rounds == 1
    # Exactly one backend call (no matcher / distill / reviewer).
    assert len(backend.calls) == 1
    assert backend.calls[0]["run_label"] == "simple-1"
    # SELF uses the same xhigh-effort default as the gpt-5.4-mini engineer route.
    assert backend.calls[0]["options"].reasoning_effort == "xhigh"


def test_execute_self_path_one_turn_no_reviewer(tmp_path: Path) -> None:
    """A SELF route answer runs one bounded Manager turn with no reviewer."""
    backend = _FakeBackend(response_message="17*23 = 391.", classify_answer="SELF")
    runner = _make_runner(backend)
    runner._args.skills_dir = str(tmp_path)  # empty store → no skill match call
    sink = _RecordingSink()

    out = runner.execute(objective="算 17*23", sink=sink)

    assert out.success is True and out.status == "done" and out.rounds == 1
    assert out.chat_mode is False  # it's a task, not chat
    assert len(backend.calls) == 1
    assert backend.calls[0]["run_label"] == "simple-1"
    assert backend.calls[0]["options"].watchdog_hard_idle_seconds == 180
    assert backend.calls[0]["options"].watchdog_soft_idle_seconds == 10
    assert callable(backend.calls[0]["options"].inactivity_callback)
    types = [e.get("type") for e in sink.events]
    assert "round.review.completed" not in types  # NO reviewer
    assert "round.review.started" not in types
    assert any(e.get("type") == "loop.done" and "(simple)" in str(e.get("text"))
               for e in sink.events)
    assert "算 17*23" in backend.calls[0]["prompt"]


def test_self_retries_empty_acp_timeout_once_and_aggregates_usage() -> None:
    class _FlakyAcpBackend(_FakeBackend):
        def run_exec(self, **kwargs: Any) -> RunnerResult:
            self.calls.append(dict(kwargs))
            if len(self.calls) == 1:
                return RunnerResult(
                    exit_code=1,
                    thread_id="stalled-session",
                    fatal_error="ACP prompt timed out after 300s",
                    input_tokens=7,
                )
            return RunnerResult(
                exit_code=0,
                thread_id="fresh-session",
                agent_messages=["落霞与孤鹜齐飞"],
                input_tokens=11,
                output_tokens=5,
            )

    backend = _FlakyAcpBackend()
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner._simple_quick_reply(objective="写滕王阁序", sink=sink)

    assert out.success is True
    assert len(backend.calls) == 2
    assert backend.calls[1]["resume_thread_id"] is None
    main = next(event for event in sink.events if event.get("type") == "round.main.completed")
    assert main["attempt_count"] == 2
    assert main["input_tokens"] == 18
    assert main["output_tokens"] == 5
    assert any(event.get("kind") == "provider_retry" for event in sink.events)


@pytest.mark.parametrize(
    "fatal_error",
    [
        "External interrupt: daemon stop requested",
        "External interrupt: operator abort requested: stop now",
        "refused before start: daily budget exhausted",
    ],
)
def test_self_never_retries_explicit_interrupts(fatal_error: str) -> None:
    from argus_skill.apps._runtime import _self_retryable_transport_failure

    result = RunnerResult(exit_code=1, fatal_error=fatal_error)
    assert _self_retryable_transport_failure(result) is False


def test_self_does_not_retry_after_tool_activity() -> None:
    from argus_skill.apps._runtime import _self_retryable_transport_failure

    result = RunnerResult(
        exit_code=1,
        fatal_error="ACP prompt timed out after 300s",
        tool_activity_observed=True,
    )
    assert _self_retryable_transport_failure(result) is False


def test_execute_team_answer_uses_full_pipeline() -> None:
    """A TEAM route answer must not short-circuit."""
    backend = _FakeBackend(
        classify_answer="TEAM",
        response_message='{"steps":[{"title":"Check the premise","detail":"decide whether it is true"},{"title":"Build the argument"},{"title":"Verify the conclusion"}]}',
    )
    runner = _make_runner(backend)
    runner.planner_backend = backend
    out = runner._maybe_chat_outcome(objective="optimize the kernel", sink=_RecordingSink())
    assert out is None


def test_execute_dispatches_to_self_path_on_chinese_capability_question() -> None:
    backend = _FakeBackend(response_message="我可以帮你读代码、改文件、跑测试。")
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner.execute(objective="你有什么能力？", sink=sink)

    assert out.chat_mode is False
    assert len(backend.calls) == 1
    # Prompt must NOT carry the engineer's Verification template (the
    # full ``## Verification (verbatim)`` heading the engineer prompt
    # produces in mission mode).
    prompt = backend.calls[0]["prompt"]
    assert "## Verification (verbatim)" not in prompt
    assert "## Required output" not in prompt
    # And it must contain the user message verbatim so codex sees it.
    assert "你有什么能力？" in prompt


def test_execute_uses_full_pipeline_on_real_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A clear engineering task must NOT short-circuit. The model
    classifier answers TEAM, so the runner falls through to the
    SkillLoop path. We assert ``_simple_quick_reply`` is NOT invoked by
    setting a sentinel that would raise if called.
    """
    backend = _FakeBackend(
        classify_answer="TEAM",
        response_message=(
            '{"steps":['
            '{"title":"Check the premise","detail":"decide whether it is true"},'
            '{"title":"Build the argument"},'
            '{"title":"Verify the conclusion"}'
            ']}'
        ),
    )
    runner = _make_runner(backend)
    runner._args.skills_dir = str(tmp_path / "global-skills")
    runner._args.project_state_dir = str(tmp_path / "project-state")
    runner.planner_backend = backend
    sink = _RecordingSink()

    # Replace the chat path with a sentinel — if the runner mistakenly
    # routes a real task into chat mode, this test fails loudly.
    sentinel_calls: list[str] = []
    def _sentinel(*, objective: str, sink: Any, seed_thread_id: Any = None):
        sentinel_calls.append(objective)
        raise AssertionError(
            f"chat fast-path was triggered for what should be a task: {objective!r}"
        )
    runner._simple_quick_reply = _sentinel

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

    planned_tasks: list[str] = []
    loop_kwargs: list[dict[str, Any]] = []

    class _StubLoop:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw
            loop_kwargs.append(kw)
        def run(self, *args: Any, **kw: Any) -> _StubLoopOutcome:
            planned_tasks.append(str(args[0]))
            return _StubLoopOutcome()

    runner._SkillLoop = _StubLoop

    @dataclass
    class _StubConfig:
        engineer_model: str = ""
        reviewer_model: str | None = None
        max_rounds: int = 1
        skill_writeback: bool = True
        skill_ops_enabled: bool = False
        wiki_ops_enabled: bool = False
        auto_init_wiki: bool = False
        session_id: str | None = None
        dangerous_yolo: bool = True
        full_auto: bool = False
        skip_git_repo_check: bool = True
        matcher_reasoning_effort: str = "high"

        def resolved_matcher_model(self) -> str:
            return self.engineer_model

    runner._SkillLoopConfig = _StubConfig

    out = runner.execute(
        objective="implement a binary tree in src/tree.py",
        sink=sink,
        mission_id="mission-tree",
    )

    assert sentinel_calls == [], "real task wrongly routed into chat fast-path"
    assert out.chat_mode is False
    assert any(call["run_label"] == "planner-bounded-plan" for call in backend.calls)
    assert planned_tasks and "## Planner execution plan (advisory)" in planned_tasks[0]
    assert "Check the premise" in planned_tasks[0]
    assert any(event.get("type") == "plan.completed" for event in sink.events)
    from argus_skill.skills.layered import LayeredSkillStore

    layered = loop_kwargs[0]["skill_store"]
    assert isinstance(layered, LayeredSkillStore)
    assert layered.project.skills_dir == tmp_path / "project-state" / "skills"
    assert layered.global_.skills_dir == tmp_path / "global-skills"
    assert loop_kwargs[0]["config"].wiki_ops_enabled is True
    assert loop_kwargs[0]["config"].auto_init_wiki is True
    assert loop_kwargs[0]["config"].session_id == "mission-tree"

    backend.calls.clear()
    planned_tasks.clear()
    runner.execute(
        objective="planner already authored this backlog item",
        sink=_RecordingSink(),
        preplanned=True,
    )
    assert not any(call["run_label"] == "planner-bounded-plan" for call in backend.calls)
    assert planned_tasks and "## Planner execution plan" not in planned_tasks[0]


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
    # Reviewer / writeback / author events must NOT appear.
    forbidden = {
        "round.review.completed",
        "skill.writeback",
        "skill.match",
        "skill.distill.start",
        "author.start",
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
    assert out.chat_mode is False


# ---------- classify_needs_continuous: BOUNDED vs STANDING auto-promote ----

def test_classify_needs_continuous_true_for_standing_answer() -> None:
    """An open-ended task ("optimize as many kernels as possible") should be
    judged STANDING, so the REPL can auto-arm continuous mode without the
    operator ever typing --continuous --objective."""
    backend = _FakeBackend(classify_answer="STANDING")
    runner = _make_runner(backend)

    assert runner.classify_needs_continuous(
        "optimize as many kernels as possible, keep going until none are left"
    ) is True
    assert backend.classify_calls[-1]["run_label"] == "router-classify-persistence"


def test_classify_needs_continuous_false_for_bounded_answer() -> None:
    """A well-scoped task with a natural finish line stays BOUNDED (one-shot)."""
    backend = _FakeBackend(classify_answer="BOUNDED")
    runner = _make_runner(backend)

    assert runner.classify_needs_continuous("fix the flaky test in test_foo.py") is False


def test_classify_needs_continuous_fails_soft_to_false_on_backend_error() -> None:
    """A classify hiccup must never force an expensive 7x24 campaign."""
    class _BoomBackend(_FakeBackend):
        def run_exec(self, **kwargs: Any) -> RunnerResult:  # noqa: ANN401
            raise RuntimeError("boom")

    runner = _make_runner(_BoomBackend())
    assert runner.classify_needs_continuous("anything") is False


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
        scope: str = "",
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
    assert [e.get("type") for e in sink.events if e.get("type", "").startswith("life.mission.")] == [
        "life.mission.started",
        "life.mission.completed",
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
                    prelude_context: str = "", scope: str = "") -> _NonChatOutcome:
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
