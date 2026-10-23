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
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from argus.core.models import RunnerOptions, RunnerResult
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import (
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
    stream_message: str | None = None
    backend: str = "pi"
    started_at: float = 0.0
    call_id: str = ""
    tool_activity_observed: bool | None = None
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
        if self.stream_message:
            on_agent_message = getattr(options, "on_agent_message", None)
            if callable(on_agent_message):
                on_agent_message(self.stream_message)
        return RunnerResult(
            exit_code=self.exit_code,
            agent_messages=[self.response_message],
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            thread_id=self.thread_id,
            fatal_error=self.fatal_error,
            started_at=self.started_at,
            call_id=self.call_id,
            tool_activity_observed=(
                self.tool_activity_observed if self.tool_activity_observed is not None
                else run_label.startswith("self-") and bool(self.response_message)
            ),
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
    from argus.apps._runtime import _SkillLoopRunner

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
    # The runner holds the single Manager instance, wired to the fake backend.
    from argus.manager import Manager

    runner.manager = Manager(project_root=Path.cwd(), runner=backend)
    return runner


@pytest.mark.parametrize("mode", ["project_status", "argus_status", "host_status"])
def test_scoped_status_reads_facts_without_another_model_turn(tmp_path, mode):
    from argus.daemon.runtime_status import register_web_status

    root = tmp_path / "projects" / "s-current"
    root.mkdir(parents=True)
    backend = _FakeBackend(response_message="Incorrect invented status")
    runner = _make_runner(backend)
    runner._manager_session_root = root
    sink = _RecordingSink()
    close = register_web_status(tmp_path, lambda sid: 1)
    try:
        outcome = runner._maybe_chat_outcome(objective="你现在的服务器上在干什么？argus", sink=sink, route="simple", self_mode=mode)
    finally:
        close()
    assert outcome.success and outcome.chat_mode and outcome.rounds == 0
    assert backend.calls == []
    reply = next(row["last_message"] for row in sink.events if row["type"] == "round.main.completed")
    assert "网页服务正在运行" in reply and "Incorrect invented status" not in reply
    assert not (root / "backlog.jsonl").exists()


def test_execute_config_loads_custom_vertical_from_session_state(
    tmp_path: Path,
) -> None:
    from argus.apps._runtime_helpers import _ExecuteState
    from argus.verticals._data_domain import write_data_domain

    state_root = tmp_path / "life"
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    write_data_domain(
        state_root,
        "physical_archive_restoration",
        stages=["condition_assessment", "restoration", "delivery"],
        status="candidate",
        purpose="physical archive restoration",
        require_independent_review=True,
    )
    from argus.core.knob_store import write_persisted_knob

    write_persisted_knob("ARGUS_SKILL_OPERATOR_AVAILABLE", "0")
    runner = _make_runner(_FakeBackend())
    runner._artifact_root = state_root
    runner._role_memory_maintenance_enabled = True
    runner._args.project_state_dir = str(state_root)
    runner._args.workdir = str(workdir)
    from argus.loop import SkillLoopConfig

    runner._SkillLoopConfig = SkillLoopConfig

    state = _ExecuteState()
    runner._build_execute_config(
        state,
        working_dir_override=str(workdir),
        maintenance_mission=False,
        vertical_override="physical_archive_restoration",
        require_independent_review=False,
        max_rounds_override=1,
        context_packet_path="",
        mission_id="custom-vertical",
        workflow_mode_override="staged",
    )

    assert state.workdir == workdir
    assert state.config.active_vertical == "physical_archive_restoration"
    assert state.config.vertical_state_root == state_root
    assert state.config.require_independent_review is True
    assert state.config.operator_questions_allowed is False
    assert state.config.state_root == state_root
    assert not (
        workdir / "research" / "DOMAINS" / "physical_archive_restoration.json"
    ).exists()

    from argus.reviewer import Reviewer

    prompt = Reviewer(_FakeBackend())._build_prompt(
        objective="Review the condition assessment scaffold.",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id="custom-vertical",
        main_summary="Implemented and tested.",
        main_error=None,
        working_dir=workdir,
        vertical_state_root=state_root,
        vertical="physical_archive_restoration",
    )
    assert "## Reviewer role" in prompt


def test_explicit_review_waiver_emits_a_visible_reason(tmp_path, caplog) -> None:
    from argus.apps._runtime_helpers import _ExecuteState
    from argus.loop import SkillLoopConfig

    workdir = tmp_path / "workspace"
    workdir.mkdir()
    runner = _make_runner(_FakeBackend())
    runner._artifact_root = tmp_path / "life"
    runner._role_memory_maintenance_enabled = True
    runner._args.project_state_dir = str(runner._artifact_root)
    runner._args.workdir = str(workdir)
    runner._SkillLoopConfig = SkillLoopConfig

    state = _ExecuteState()
    with caplog.at_level("WARNING", logger="argus.apps._runtime_execute"):
        runner._build_execute_config(
            state,
            working_dir_override=str(workdir),
            maintenance_mission=True,
            vertical_override="",
            require_independent_review=False,
            max_rounds_override=1,
            context_packet_path="",
            mission_id="maintenance-waiver",
            workflow_mode_override="direct",
        )

    assert "independent review waived: framework maintenance mission" in caplog.text


def test_a_sandboxed_engineer_may_read_its_own_handoff_directory(tmp_path) -> None:
    """2026-10-20: safe mode denied the Engineer the mission and latest handoff
    its prompt pointed at. It is given that task's directory, not the state root."""
    from argus.apps._runtime_helpers import _ExecuteState
    from argus.loop import SkillLoopConfig

    workdir = tmp_path / "workspace"
    workdir.mkdir()
    state_root = tmp_path / "life"
    handoff = state_root / "handoffs" / "task-1"
    handoff.mkdir(parents=True)
    runner = _make_runner(_FakeBackend())
    runner._artifact_root = state_root
    runner._role_memory_maintenance_enabled = True
    runner._args.project_state_dir = str(state_root)
    runner._args.workdir = str(workdir)
    runner._SkillLoopConfig = SkillLoopConfig

    state = _ExecuteState()
    runner._build_execute_config(
        state,
        working_dir_override=str(workdir),
        maintenance_mission=False,
        vertical_override="software",
        require_independent_review=True,
        max_rounds_override=1,
        context_packet_path=str(handoff / "mission.json"),
        mission_id="task-1",
        workflow_mode_override="direct",
    )

    assert str(handoff.resolve()) in state.config.add_dirs
    assert str(state_root.resolve()) not in state.config.add_dirs


# ---------- Manager SELF fast-path: runner unit tests ----------------------

def test_self_route_answers_a_greeting_in_one_manager_turn(monkeypatch) -> None:
    """English greeting → one Manager turn, no team pipeline."""
    monkeypatch.delenv("ARGUS_SKILL_SELF_REASONING_EFFORT", raising=False)
    monkeypatch.setattr(
        "argus.apps._self_reply.resolve_role_model",
        lambda *_args, **_kwargs: "best-manager",
    )
    backend = _FakeBackend(response_message="Hi! How can I help?")
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner._maybe_chat_outcome(objective="hello", sink=sink, route="simple")

    assert out.chat_mode is False
    assert out.success is True
    assert out.status == "done"
    assert out.rounds == 1
    # Exactly one backend call (no matcher / distill / reviewer).
    assert len(backend.calls) == 1
    assert backend.calls[0]["run_label"] == "simple-1"
    # Foreground inspection is latency-sensitive and no longer defaults to the
    # maximum reasoning tier; operators can still request xhigh explicitly.
    assert backend.calls[0]["options"].reasoning_effort == "high"
    assert backend.calls[0]["options"].model == "best-manager"


def test_message_only_self_reply_uses_manager_model_with_low_effort(monkeypatch) -> None:
    monkeypatch.setattr(
        "argus.apps._self_reply.resolve_role_model",
        lambda *_args, **_kwargs: "persistent-manager-model",
    )
    backend = _FakeBackend(response_message="exact reply")
    runner = _make_runner(backend)

    runner._simple_quick_reply(
        objective="reply exactly hello",
        sink=_RecordingSink(),
        lean=True,
    )

    call = backend.calls[-1]
    assert call["run_label"] == "manager-quick-reply"
    assert call["options"].model == "persistent-manager-model"
    assert call["options"].reasoning_effort == "low"
    assert call["options"].dangerous_yolo is False
    assert "reply exactly hello" in call["prompt"]
    assert "Grounding workspace" not in call["prompt"]
    assert "Current project evidence" in call["prompt"]


def test_local_microtask_uses_compact_isolated_execution(monkeypatch) -> None:
    monkeypatch.setattr(
        "argus.apps._self_reply.resolve_role_reasoning_effort",
        lambda *_args, **_kwargs: "high",
    )
    backend = _FakeBackend(response_message="done")
    runner = _make_runner(backend)

    runner._simple_quick_reply(
        objective="create result.txt and verify it",
        sink=_RecordingSink(),
        execute_mode="micro",
    )

    call = backend.calls[-1]
    assert call["run_label"] == "self-micro"
    assert call["prompt"].startswith("create result.txt and verify it\n\n")
    assert call["options"].model == "gpt-5.4-mini"
    assert call["options"].reasoning_effort == "high"
    assert call["options"].skill_paths == []
    assert call["options"].extra_args == [
        "--tools",
        "bash",
        "--system-prompt",
        "Complete the finite local microtask in the current directory. Inspect the relevant "
        "local inputs in tool output before drafting or extracting facts; do not guess their contents "
        "or format. Then make the requested change and verify it. Use only necessary "
        "tool calls, report briefly, and stop.",
    ]
    assert runner.last_thread_id is None


@pytest.mark.parametrize("learning", ["1", "0"])
def test_self_turn_is_told_to_keep_its_learning_only_while_learning_is_on(tmp_path, monkeypatch, learning):
    monkeypatch.setenv("ARGUS_SKILL_LEARNING", learning)
    workspace = tmp_path / "workspace"
    wiki = workspace / ".autors" / "p" / "wiki"
    (wiki / "pages").mkdir(parents=True)
    (wiki / "INDEX.md").write_text("# Index\n", encoding="utf-8")
    backend = _FakeBackend(response_message="Fixed and checked.")
    runner = _make_runner(backend)
    runner._args.workdir = str(workspace)

    runner._simple_quick_reply(objective="fix the function", sink=_RecordingSink(), execute_mode="implement")

    prompt = backend.calls[-1]["prompt"]
    assert len(backend.calls) == 1
    assert (f"`{workspace / '.agents' / 'skills'}/<name>/SKILL.md`" in prompt) is (learning == "1")
    assert (f"project Wiki `{wiki}`" in prompt) is (learning == "1")


@pytest.mark.parametrize("mode", ["micro", "implement", "debug", "review", "synthesize", "inspect", "reply"])
def test_every_single_worker_mode_receives_selected_vertical(tmp_path, mode):
    from argus.manager import Manager
    from argus.skills.layered import LayeredSkillStore

    backend = _FakeBackend(response_message="Verified the requested result.")
    runner = _make_runner(backend)
    root = tmp_path / "state"
    runner._args.workdir = str(tmp_path / "workspace")
    runner.manager = Manager(root, runner=backend, execution_workdir=runner._args.workdir,
                            skill_store=LayeredSkillStore(project_dir=root / "skills", global_dir=tmp_path / "skills"))
    outcome = runner._maybe_chat_outcome(objective="Check this function", sink=_RecordingSink(),
                                        route="simple", self_mode=mode, skill_vertical="software")
    assert outcome.success
    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert "SOFTWARE VERTICAL" in call["prompt"]
    assert "Check this function" in call["prompt"]
    assert str(tmp_path / "skills/_shared_verticals/software/engineer") in call["options"].skill_paths
    assert call["options"].disable_tools is False
    assert not (root / "backlog.jsonl").exists()


def test_web_self_classification_reaches_execution_and_atlas_without_team(tmp_path, monkeypatch):
    import threading

    from fastapi.testclient import TestClient

    from argus.core.role_actions import PREFIX
    from argus.core.role_tool_bridge import bridge_request
    from argus.core.session import SessionMeta, write_session_meta
    from argus.manager import Manager, config_intent, front_door
    from argus.skills.layered import LayeredSkillStore
    from argus.webapi import manager_state, server

    # Learning stays on: the SELF turn keeps what it learned itself, so the
    # turn is the only model call after classification.
    monkeypatch.setenv("ARGUS_SKILL_LEARNING", "1")
    classify_calls: list[dict] = []

    class Backend(_FakeBackend):
        def run_exec(self, **kwargs):
            if kwargs["run_label"] == "manager-frontdoor-classify":
                classify_calls.append(kwargs)
                bridge_request(PREFIX, "hand_off", {
                    "to": "manager", "self_mode": "implement", "name": "Correct a function",
                    "intake": {"type": "objective_amendment", "scope": "mission"},
                    "domain": {"vertical": "software"},
                }, env=kwargs["options"].extension_env)
                return RunnerResult(exit_code=0)
            # The runner's stream relay, as the real backend's event callback drives it.
            runner._stream_progress_relay(runner._current_sink, None, f"{kwargs['run_label']}.stdout", json.dumps({
                "type": "tool.execution_start",
                "data": {"toolCallId": "call-1", "toolName": "bash", "arguments": {"command": "pytest -q"}},
            }))
            return super().run_exec(**kwargs)

    sid = "s-self-vertical"
    state = tmp_path / "projects" / sid
    state.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(workspace), workdir=str(workspace)))
    backend = Backend(response_message="Fixed and checked the function.")
    runner = _make_runner(backend)
    from argus.adapters.stream_progress import StreamProgressRelay

    runner._stream_progress_relay = StreamProgressRelay()
    runner._args.workdir = str(workspace)
    runner._manager_session_root = state
    runner.manager = Manager(state, runner=backend, execution_workdir=workspace,
                            learned_vertical_root=tmp_path,
                            skill_store=LayeredSkillStore(project_dir=state / "skills", global_dir=tmp_path / "skills"))
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: runner)
    monkeypatch.setattr(config_intent, "_ensure_manager_runner", lambda *_a: runner)
    manager_state._STATES.pop(sid, None)
    before = set(threading.enumerate())
    with TestClient(server.create_app(global_root=tmp_path)) as client:
        result = client.post(f"/api/projects/{sid}/message", json={"text": "Fix this one function"}).json()
        assert result["kind"] == "chat" and result["success"] is True, result
        assert "decision_card" not in result
        atlas = client.get(f"/api/projects/{sid}/map").json()
        assert any(task["kind"] == "turn" and task["status"] == "done" for task in atlas["tasks"])
    for thread in set(threading.enumerate()) - before:
        if thread.name.startswith("argus-"):
            thread.join(timeout=5)
    assert len(classify_calls) == 1 and len(backend.calls) == 1
    assert "single-agent task: software" in backend.calls[0]["prompt"]
    assert f"`{workspace / '.agents' / 'skills'}/<name>/SKILL.md`" in backend.calls[0]["prompt"]
    # The self-implement turn's tool call is on record as a step of its turn.
    events = [json.loads(line) for line in (state / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    steps = [event for event in events if event.get("turn_step")]
    assert steps and {(event["agent_layer"], event["kind"]) for event in steps} == {("manager", "command_execution")}
    assert str(tmp_path / "skills/_shared_verticals/software/engineer") in backend.calls[0]["options"].skill_paths
    assert not (state / "backlog.jsonl").exists() or not (state / "backlog.jsonl").read_text().strip()
    manager_state._STATES.pop(sid, None)


def test_local_microtask_returns_delivery_for_named_workspace_file(redact_secrets_on, 
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    (workdir / "result.txt").write_text("done\n", encoding="utf-8")
    backend = _FakeBackend(
        response_message="Created `result.txt`. api_key=abcdefghijk",
        started_at=time.time() - 1,
        call_id="solo-call-1",
    )
    runner = _make_runner(backend)
    runner._args.workdir = str(workdir)

    outcome = runner._simple_quick_reply(
        objective="create result.txt",
        sink=_RecordingSink(),
        execute_mode="micro",
        root_task_id="solo-turn-1",
    )

    assert outcome.delivery is not None
    assert outcome.delivery["review_status"] == "not_assessed"
    assert outcome.delivery["title"] == "result.txt"
    assert outcome.delivery["primary_target"]["path"] == "result.txt"
    assert outcome.delivery["primary_target"]["source"] == "solo_output"
    assert outcome.delivery["delivery_id"].startswith("delivery:solo-call-1:")
    assert "abcdefghijk" not in outcome.delivery["summary"]


def test_research_answer_saved_as_a_report_is_a_delivery(tmp_path: Path) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    (workdir / "survey.md").write_text("# Survey\n\nFindings with sources.\n", encoding="utf-8")
    backend = _FakeBackend(
        response_message="Summary of the survey; full report in `survey.md`.",
        started_at=time.time() - 1,
        call_id="research-call-1",
    )
    runner = _make_runner(backend)
    runner._args.workdir = str(workdir)

    outcome = runner._simple_quick_reply(
        objective="research the field and write me a survey",
        sink=_RecordingSink(),
        root_task_id="research-turn-1",
    )

    assert outcome.delivery is not None
    assert outcome.delivery["primary_target"]["path"] == "survey.md"
    # Nobody independently reviewed a solo research answer.
    assert outcome.delivery["review_status"] == "not_assessed"
    # The worker is told when a written report is worth keeping as a file.
    assert "save it as a Markdown file" in backend.calls[0]["prompt"]


def test_local_worker_does_not_deliver_an_unchanged_existing_file(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    (workdir / "README.md").write_text("existing\n", encoding="utf-8")
    backend = _FakeBackend(
        response_message="Reviewed `README.md`.",
        started_at=time.time() + 1,
    )
    runner = _make_runner(backend)
    runner._args.workdir = str(workdir)

    outcome = runner._simple_quick_reply(
        objective="review README.md",
        sink=_RecordingSink(),
        execute_mode="review",
        root_task_id="solo-turn-2",
    )

    assert outcome.delivery is None


def test_text_only_self_reply_has_no_delivery() -> None:
    runner = _make_runner(_FakeBackend(response_message="Plain answer."))

    outcome = runner._simple_quick_reply(
        objective="answer briefly",
        sink=_RecordingSink(),
        lean=True,
    )

    assert outcome.delivery is None


@pytest.mark.parametrize(
    ("mode", "tools", "prompt_fragment"),
    [
        ("implement", "read,bash,edit,write", "implementation task"),
        ("debug", "read,bash,edit,write", "debugging task"),
        ("review", "read,bash,write", "without modifying source"),
        ("synthesize", "read,bash,write", "supplied-source synthesis"),
    ],
)
def test_local_worker_modes_get_narrow_tools_and_prompts(
    mode: str,
    tools: str,
    prompt_fragment: str,
) -> None:
    backend = _FakeBackend(response_message="done")
    runner = _make_runner(backend)

    runner._simple_quick_reply(
        objective="complete the local task",
        sink=_RecordingSink(),
        execute_mode=mode,
    )

    call = backend.calls[-1]
    assert call["run_label"] == f"self-{mode}"
    assert call["prompt"].startswith("complete the local task\n\n")
    assert call["options"].extra_args[:2] == ["--tools", tools]
    assert prompt_fragment in call["options"].extra_args[-1]


def test_local_worker_mode_reads_what_argus_already_knows() -> None:
    backend = _FakeBackend(response_message="done")
    runner = _make_runner(backend)
    asked: list[tuple[str, str, str]] = []

    def recall(objective: str, *, role: str, mission_id: str) -> str:
        asked.append((objective, role, mission_id))
        return "### What Argus already knows (advisory)\n- page: `/wiki/pages/scheduler.md`"

    runner._args.manager_memory = SimpleNamespace(render_recall_context=recall)

    runner._simple_quick_reply(
        objective="design the scheduler", sink=_RecordingSink(), execute_mode="implement",
        root_task_id="turn-7",
    )

    # The SELF turn's recall is recorded as the Manager's, for this task.
    assert asked == [("design the scheduler", "manager", "turn-7")]
    assert backend.calls[-1]["options"].add_dirs == []
    assert backend.calls[-1]["prompt"].startswith(
        "### What Argus already knows (advisory)\n- page: `/wiki/pages/scheduler.md`"
        "\n\ndesign the scheduler\n\n"
    )


@pytest.mark.parametrize("mode", ["implement", ""])
def test_direct_work_may_read_the_libraries_recall_points_at(monkeypatch, tmp_path, mode) -> None:
    calls: list[tuple] = []

    def read_dirs(state, global_root, workspace):
        calls.append((state, global_root, workspace))
        return ["/elsewhere/wiki"]

    monkeypatch.setattr("argus.life.knowledge_recall.knowledge_read_dirs", read_dirs)
    backend = _FakeBackend(response_message="done")
    runner = _make_runner(backend)
    runner._args.workdir = str(tmp_path)
    runner._args.manager_memory = SimpleNamespace(
        project_root=tmp_path / "state", global_root=tmp_path / "home",
        render_recall_context=lambda *_a, **_k: "", render_prelude=lambda **_k: "",
    )

    runner._simple_quick_reply(objective="design the scheduler", sink=_RecordingSink(), execute_mode=mode)

    assert calls == [(tmp_path / "state", tmp_path / "home", tmp_path)]
    assert "/elsewhere/wiki" in backend.calls[-1]["options"].add_dirs


def test_manager_self_effort_can_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_SELF_REASONING_EFFORT", "high")
    backend = _FakeBackend(response_message="grounded")
    runner = _make_runner(backend)

    runner._simple_quick_reply(
        objective="inspect the current state",
        sink=_RecordingSink(),
    )

    assert backend.calls[-1]["options"].reasoning_effort == "high"


def test_status_like_self_turn_uses_manager_model(tmp_path: Path) -> None:
    backend = _FakeBackend(response_message="status from manager")
    runner = _make_runner(backend)
    runner._manager_session_root = tmp_path
    sink = _RecordingSink()

    out = runner._maybe_chat_outcome(
        objective="你现在的进度如何",
        sink=sink,
        route="simple",
    )

    assert out is not None and out.success is True and out.rounds == 1
    assert len(backend.calls) == 1
    assert backend.calls[0]["run_label"] == "simple-1"
def test_self_route_runs_one_turn_without_reviewer(tmp_path: Path) -> None:
    """A SELF route answer runs one bounded Manager turn with no reviewer."""
    backend = _FakeBackend(response_message="17*23 = 391.")
    runner = _make_runner(backend)
    runner._args.skills_dir = str(tmp_path)  # empty store → no skill match call
    sink = _RecordingSink()

    out = runner._maybe_chat_outcome(objective="算 17*23", sink=sink, route="simple")

    assert out.success is True and out.status == "done" and out.rounds == 1
    assert out.chat_mode is False  # it's a task, not chat
    assert len(backend.calls) == 1
    assert backend.calls[0]["run_label"] == "simple-1"
    # The operator can cancel an interactive turn; no timer stops it.
    assert not backend.calls[0]["options"].watchdog_hard_idle_seconds
    assert backend.calls[0]["options"].watchdog_soft_idle_seconds == 5
    assert callable(backend.calls[0]["options"].inactivity_callback)
    assert backend.calls[0]["options"].sandbox_mode is None
    assert backend.calls[0]["options"].dangerous_yolo is True
    assert backend.calls[0]["options"].full_auto is False
    types = [e.get("type") for e in sink.events]
    assert "round.review.completed" not in types  # NO reviewer
    assert "round.review.started" not in types
    assert {"type": "loop.done", "status": "done", "rounds": 1} in sink.events
    assert "算 17*23" in backend.calls[0]["prompt"]
    assert "Answer the request yourself" in backend.calls[0]["prompt"]
    assert "Runtime maintenance must use an isolated worktree" not in backend.calls[0]["prompt"]


def test_manager_self_progress_blocks_redacted_before_live_sink(redact_secrets_on) -> None:
    secret = "ghp_" + "A" * 36
    backend = _FakeBackend(
        response_message=f"final token {secret}",
        stream_message=f"streamed token {secret}",
    )
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner._maybe_chat_outcome(
        objective="hello",
        sink=sink,
        route="simple",
    )

    payload = json.dumps({
        "outcome": getattr(out, "last_message", ""),
        "events": sink.events,
    }, ensure_ascii=False)
    assert secret not in payload
    assert "REDACTED" in payload


def test_self_prompt_includes_latest_queued_operator_objective(
    tmp_path: Path,
) -> None:
    memory = LifeMemory.open(tmp_path)
    memory.backlog.add(BacklogItem.new(
        title="implement durable checkpoints",
        objective="Implement durable checkpoints for interrupted experiments",
    ))
    backend = _FakeBackend(response_message="continuing the queued task")
    runner = _make_runner(backend)
    runner._manager_session_root = tmp_path

    runner._simple_quick_reply(
        objective="继续",
        sink=_RecordingSink(),
    )

    prompt = backend.calls[-1]["prompt"]
    assert "Implement durable checkpoints for interrupted experiments" in prompt


def test_self_prompt_injects_authoritative_team_log_path(tmp_path: Path) -> None:
    backend = _FakeBackend(response_message="grounded in the team log")
    runner = _make_runner(backend)
    runner._manager_session_root = tmp_path

    runner._simple_quick_reply(
        objective="What did the Team conclude?",
        sink=_RecordingSink(),
    )

    call = backend.calls[-1]
    expected = tmp_path / "events.jsonl"
    assert f"Authoritative Team log: {expected}" in call["prompt"]
    assert "read that log yourself before answering" in call["prompt"]
    assert call["options"].add_dirs == [str(tmp_path)]


def test_self_grounds_in_operator_workspace_without_moving_state_root(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    workspace = tmp_path / "workspace"
    state_root.mkdir()
    workspace.mkdir()
    backend = _FakeBackend(response_message="grounded")
    runner = _make_runner(backend)
    runner._args.workdir = str(state_root)
    runner._args.operator_workspace = str(workspace)

    runner._simple_quick_reply(
        objective="inspect the current source tree",
        sink=_RecordingSink(),
    )

    call = backend.calls[-1]
    assert call["options"].working_dir == str(workspace)
    assert f"Operator launch workspace: {workspace}" in call["prompt"]


def test_self_timeout_returns_visible_failure_without_second_long_wait() -> None:
    class _FlakyBackend(_FakeBackend):
        def run_exec(self, **kwargs: Any) -> RunnerResult:
            self.calls.append(dict(kwargs))
            if len(self.calls) == 1:
                return RunnerResult(
                    exit_code=1,
                    thread_id="stalled-session",
                    fatal_error="prompt timed out after 300s",
                    input_tokens=7,
                )
            return RunnerResult(
                exit_code=0,
                thread_id="fresh-session",
                agent_messages=["落霞与孤鹜齐飞"],
                input_tokens=11,
                output_tokens=5,
            )

    backend = _FlakyBackend()
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner._simple_quick_reply(objective="写滕王阁序", sink=sink)

    assert out.success is False
    assert out.status == "error"
    assert "timed out" in out.stop_reason
    assert len(backend.calls) == 1
    main = next(event for event in sink.events if event.get("type") == "round.main.completed")
    assert main["attempt_count"] == 1
    assert main["input_tokens"] == 7
    assert main["output_tokens"] == 0
    assert not any(event.get("kind") == "provider_retry" for event in sink.events)


def test_self_retries_empty_opencode_endpoint_failure_once() -> None:
    class _FlakyOpenCodeBackend(_FakeBackend):
        def run_exec(self, **kwargs: Any) -> RunnerResult:
            self.calls.append(dict(kwargs))
            if len(self.calls) == 1:
                return RunnerResult(
                    exit_code=1,
                    fatal_error=(
                        "Error from provider (Console): Upstream request failed: "
                        "Endpoint is unavailable."
                    ),
                )
            return RunnerResult(
                exit_code=0,
                thread_id="fresh-session",
                agent_messages=["implemented"],
            )

    backend = _FlakyOpenCodeBackend()
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner._simple_quick_reply(objective="implement the task", sink=sink)

    assert out.success is True
    assert len(backend.calls) == 2
    assert backend.calls[1]["resume_thread_id"] is None
    assert any(event.get("kind") == "provider_retry" for event in sink.events)


def test_self_retries_empty_success_then_returns_explicit_error() -> None:
    backend = _FakeBackend(response_message="")
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner._simple_quick_reply(objective="这个进程还活着吗", sink=sink)

    assert len(backend.calls) == 2
    assert out.success is False
    assert out.status == "error"
    assert "without an assistant message" in out.stop_reason
    main = next(
        event for event in sink.events
        if event.get("type") == "round.main.completed"
    )
    assert main["attempt_count"] == 2
    assert main["turn_completed"] is False


@pytest.mark.parametrize(
    "fatal_error",
    [
        "External interrupt: daemon stop requested",
        "External interrupt: operator abort requested: stop now",
        "refused before start: daily budget exhausted",
    ],
)
def test_self_never_retries_explicit_interrupts(fatal_error: str) -> None:
    from argus.apps._self_reply import (
        self_retryable_transport_failure as _self_retryable_transport_failure,
    )

    result = RunnerResult(exit_code=1, fatal_error=fatal_error)
    assert _self_retryable_transport_failure(result) is False


def test_self_does_not_retry_after_tool_activity() -> None:
    from argus.apps._self_reply import (
        self_retryable_transport_failure as _self_retryable_transport_failure,
    )

    result = RunnerResult(
        exit_code=1,
        fatal_error="prompt timed out after 300s",
        tool_activity_observed=True,
    )
    assert _self_retryable_transport_failure(result) is False



def test_self_resumes_the_same_thread_once_after_a_watchdog_stall() -> None:
    """A stalled provider is retried by continuing the thread, not redoing tools.

    Stable web trial project s-701ae84b (2026-09-16): the Manager ran two
    read-only tool calls, then Gemini streamed nothing for 120s, the watchdog
    killed the turn and the operator saw "Answer failed" with no retry.
    """

    class _StallingBackend(_FakeBackend):
        def run_exec(self, **kwargs: Any) -> RunnerResult:
            self.calls.append(dict(kwargs))
            if len(self.calls) == 1:
                return RunnerResult(
                    exit_code=1,
                    thread_id="stalled-session",
                    fatal_error=(
                        "Forced restart after hard idle timeout "
                        "(120s without a model stream event)."
                    ),
                    tool_activity_observed=True,
                    input_tokens=9,
                )
            return RunnerResult(
                exit_code=0,
                thread_id="stalled-session",
                agent_messages=["这是继续完成的回答。"],
                input_tokens=4,
                output_tokens=6,
            )

    backend = _StallingBackend()
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner._simple_quick_reply(objective="给我写一篇 ICLR 论文的提纲", sink=sink)

    assert out.success is True
    assert len(backend.calls) == 2
    assert backend.calls[1]["resume_thread_id"] == "stalled-session"
    assert "Do not repeat tool calls" in backend.calls[1]["prompt"]
    retry = next(event for event in sink.events if event.get("kind") == "provider_retry")
    assert "接着刚才的对话再试一次" in retry["text"]
    main = next(event for event in sink.events if event.get("type") == "round.main.completed")
    assert main["attempt_count"] == 2


def test_self_does_not_blindly_redo_tools_after_a_stall_without_a_thread() -> None:
    class _StallingBackend(_FakeBackend):
        def run_exec(self, **kwargs: Any) -> RunnerResult:
            self.calls.append(dict(kwargs))
            return RunnerResult(
                exit_code=1,
                thread_id=None,
                fatal_error="Forced restart after hard idle timeout (120s without a model stream event).",
                tool_activity_observed=True,
            )

    backend = _StallingBackend()
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner._simple_quick_reply(objective="整理一下工作区", sink=sink)

    assert out.success is False
    assert len(backend.calls) == 1
    assert "hard idle timeout" in out.stop_reason
    assert not any(event.get("kind") == "provider_retry" for event in sink.events)


def test_stall_predicate_ignores_answered_turns_and_other_errors() -> None:
    from argus.apps._self_reply import self_stalled_model_turn as _self_stalled_model_turn

    stalled = RunnerResult(exit_code=1, fatal_error="Forced restart after hard idle timeout (120s without a model stream event).")
    assert _self_stalled_model_turn(stalled) is True
    answered = RunnerResult(exit_code=1, fatal_error="Forced restart after hard idle timeout (120s).", agent_messages=["done"])
    assert _self_stalled_model_turn(answered) is False
    assert _self_stalled_model_turn(RunnerResult(exit_code=1, fatal_error="prompt timed out after 300s")) is False
    assert _self_stalled_model_turn(RunnerResult(exit_code=0)) is False

def test_team_route_takes_no_self_turn() -> None:
    """A TEAM route answer must not short-circuit."""
    backend = _FakeBackend(
        response_message='{"steps":[{"title":"Check the premise","detail":"decide whether it is true"},{"title":"Build the argument"},{"title":"Verify the conclusion"}]}',
    )
    runner = _make_runner(backend)
    runner.planner_backend = backend
    out = runner._maybe_chat_outcome(objective="optimize the kernel", sink=_RecordingSink(), route="complex")
    assert out is None and not backend.calls


def test_self_route_answers_a_chinese_capability_question() -> None:
    backend = _FakeBackend(response_message="我可以帮你读代码、改文件、跑测试。")
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner._maybe_chat_outcome(objective="你有什么能力？", sink=sink, route="simple")

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
    """A mission runs the SkillLoop path with the session's configuration."""
    backend = _FakeBackend(
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

    # Build a minimal SkillLoop / SkillLoopConfig stub so the fall-through
    # path doesn't try to construct a real one. We replace ``_SkillLoop``
    # with a factory returning a stub whose ``run`` returns a duck-typed
    # outcome.
    @dataclass
    class _StubLoopOutcome:
        successful: bool = True
        status: str = "done"
        round_count: int = 1
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
        wiki_enabled: bool = False
        auto_init_wiki: bool = False
        session_id: str | None = None
        dangerous_yolo: bool = True
        full_auto: bool = False
        skip_git_repo_check: bool = True
        workflow_mode: str = "staged"
        active_vertical: str = ""
        paper_mission: bool = False
        reviewer_reasoning_effort: str = "xhigh"

    runner._SkillLoopConfig = _StubConfig

    out = runner.execute(
        objective=(
            "implement a binary tree in src/tree.py\n\n"
            "## Manager project grounding (advisory evidence)\n"
            "Closest analogue: src/ordered_tree.py"
        ),
        original_objective="implement a binary tree in src/tree.py",
        sink=sink,
        mission_id="mission-tree",
    )

    assert out.chat_mode is False
    # The campaign's Planner plans; a mission drafts no outline of its own.
    assert planned_tasks and "Closest analogue: src/ordered_tree.py" in planned_tasks[0]
    assert "## Planner execution plan" not in planned_tasks[0]
    assert not any(event.get("type") == "life.planner.start" for event in sink.events)
    from argus.skills.layered import LayeredSkillStore

    layered = loop_kwargs[0]["skill_store"]
    assert isinstance(layered, LayeredSkillStore)
    assert layered.project.skills_dir == tmp_path / "project-state" / "skills"
    assert layered.global_.skills_dir == tmp_path / "global-skills"
    assert loop_kwargs[0]["config"].wiki_enabled is True
    assert loop_kwargs[0]["config"].auto_init_wiki is True
    assert loop_kwargs[0]["config"].session_id == "mission-tree"

    from argus.apps import _runtime_supervisor

    monkeypatch.setattr(
        _runtime_supervisor,
        '_workflow_mode_for_project_root',
        lambda root: "direct",
    )
    monkeypatch.setattr(
        _runtime_supervisor,
        '_paper_mission_for_project_root',
        lambda root: True,
    )
    backend.calls.clear()
    planned_tasks.clear()
    loop_kwargs.clear()
    runner.execute(
        objective="write one short poem",
        sink=_RecordingSink(),
    )
    assert loop_kwargs[0]["config"].workflow_mode == "direct"
    assert loop_kwargs[0]["config"].paper_mission is False
    assert loop_kwargs[0]["config"].reviewer_reasoning_effort == "high"
    assert loop_kwargs[0]["config"].wiki_enabled is True
    assert loop_kwargs[0]["config"].auto_init_wiki is True

    backend.calls.clear()
    planned_tasks.clear()
    loop_kwargs.clear()
    runner.execute(
        objective="planner already authored this backlog item",
        sink=_RecordingSink(),
        preplanned=True,
        max_rounds_override=1,
        workflow_mode_override="direct",
    )
    assert planned_tasks
    assert loop_kwargs[0]["config"].max_rounds == 1
    assert loop_kwargs[0]["config"].workflow_mode == "direct"
    assert loop_kwargs[0]["config"].auto_init_wiki is True

    planned_tasks.clear()
    loop_kwargs.clear()
    runner.execute(
        objective="run one direct maintenance check",
        sink=_RecordingSink(),
        preplanned=True,
        vertical_override="argus_maintenance",
    )
    assert planned_tasks
    assert loop_kwargs[0]["config"].active_vertical == "argus_maintenance"
    assert loop_kwargs[0]["config"].workflow_mode == "direct"

    planned_tasks.clear()
    loop_kwargs.clear()
    monkeypatch.setenv("ARGUS_SKILL_LEARNING", "0")
    runner.execute(
        objective="run without project Wiki",
        sink=_RecordingSink(),
        preplanned=True,
        workflow_mode_override="direct",
    )
    assert loop_kwargs[0]["config"].wiki_enabled is False
    assert loop_kwargs[0]["config"].auto_init_wiki is False


def test_chat_path_emits_minimum_event_sequence() -> None:
    """REPL renderer + cost-tracking sink need a tight event set."""
    backend = _FakeBackend(input_tokens=512, output_tokens=64)
    runner = _make_runner(backend)
    sink = _RecordingSink()

    runner._maybe_chat_outcome(objective="hi", sink=sink, route="simple")

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

    runner._maybe_chat_outcome(objective="hello", sink=sink, route="simple")

    main = next(e for e in sink.events if e.get("type") == "round.main.completed")
    assert main["input_tokens"] == 412
    assert main["output_tokens"] == 37


def test_chat_path_chains_thread_id_for_session_continuity() -> None:
    backend = _FakeBackend(thread_id="tid-from-codex")
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner._maybe_chat_outcome(objective="hello", sink=sink, route="simple")

    assert out.last_thread_id == "tid-from-codex"
    assert runner.last_thread_id == "tid-from-codex"
    # Next call must resume from the previous thread by default.
    backend.calls.clear()
    runner._maybe_chat_outcome(objective="hi again", sink=sink, route="simple")
    assert backend.calls[0]["resume_thread_id"] == "tid-from-codex"


def test_chat_path_marks_status_error_on_codex_failure() -> None:
    backend = _FakeBackend(exit_code=1, fatal_error="codex died")
    runner = _make_runner(backend)
    sink = _RecordingSink()

    out = runner._maybe_chat_outcome(objective="hi", sink=sink, route="simple")

    assert out.success is False
    assert out.status == "error"
    assert "codex died" in out.stop_reason
    assert out.chat_mode is False









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


def test_local_execution_retries_plan_once_and_does_not_claim_completion():
    backend = _FakeBackend(
        response_message="We need modify project. First inspect files and use tools.",
        tool_activity_observed=False,
    )
    sink = _RecordingSink()
    outcome = _make_runner(backend)._simple_quick_reply(
        objective="Create and verify the requested report", sink=sink, execute_mode="implement",
    )
    assert len(backend.calls) == 2
    assert "no tool calls" in backend.calls[1]["prompt"]
    assert outcome.success is False
    assert outcome.delivery is None
    assert "without performing any tool action" in outcome.stop_reason


def test_local_execution_recovers_from_plan_before_any_side_effect(tmp_path):
    class RecoveringBackend(_FakeBackend):
        def run_exec(self, **kwargs):
            if self.calls:
                (tmp_path / "result.txt").write_text("actual output")
                self.response_message = "Created and checked `result.txt`."
                self.tool_activity_observed = True
            return super().run_exec(**kwargs)

    backend = RecoveringBackend(
        response_message="I will create result.txt.", tool_activity_observed=False,
        started_at=time.time() - 1, call_id="recovered-action",
    )
    runner = _make_runner(backend)
    runner._args.workdir = str(tmp_path)
    outcome = runner._simple_quick_reply(
        objective="Create result.txt", sink=_RecordingSink(), execute_mode="implement",
    )
    assert len(backend.calls) == 2
    assert outcome.success is True
    assert outcome.delivery["primary_target"]["path"] == "result.txt"
    assert (tmp_path / "result.txt").read_text() == "actual output"
