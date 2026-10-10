"""`/ask` is the stated-intent path: answer, queue nothing, involve no one else.

Argus classifies free text as chat-or-task and deliberately biases toward
task, because silently answering something meant to be done is worse than
doing something meant as a question. The cost is that a quick question can
still buy a classify call and, when the classifier plays safe, a queued item
and a full four-role round.

`/ask` removes the guess rather than making the classifier more willing to
skip work — which is what keeps the automatic path safe to leave conservative.
Shared intake still classifies the input before answering; its task routing
decision must never turn an explicit question into queued work.
"""
from __future__ import annotations

import pytest

from argus.manager.ask_intent import ASK_PREFIXES, strip_ask_prefix

# -- recognising the intent -------------------------------------------------

@pytest.mark.parametrize("prefix", ASK_PREFIXES)
def test_every_documented_prefix_is_recognised(prefix) -> None:
    assert strip_ask_prefix(f"{prefix} what backends are configured?") == (
        "what backends are configured?"
    )


def test_the_prefix_is_case_insensitive() -> None:
    assert strip_ask_prefix("/ASK what is the status") == "what is the status"


def test_a_telegram_bot_mention_is_tolerated() -> None:
    # Telegram appends @botname to commands in group chats.
    assert strip_ask_prefix("/ask@argusbot how do I add a vertical") == (
        "how do I add a vertical"
    )


def test_surrounding_whitespace_is_ignored() -> None:
    assert strip_ask_prefix("   /ask   spaced out   ") == "spaced out"


# -- what must NOT be treated as a question --------------------------------

def test_ordinary_text_is_left_alone() -> None:
    # The whole point is that inference is not involved: only the explicit
    # prefix routes to an inline answer.
    assert strip_ask_prefix("what backends are configured?") is None
    assert strip_ask_prefix("please read the literature and summarise it") is None


def test_a_bare_prefix_is_not_a_question() -> None:
    # `/ask` with no body would send the Manager an empty prompt; fall through
    # to normal handling instead.
    for prefix in ASK_PREFIXES:
        assert strip_ask_prefix(prefix) is None
        assert strip_ask_prefix(f"{prefix}   ") is None


def test_other_commands_are_untouched() -> None:
    assert strip_ask_prefix("/task build the thing") is None
    assert strip_ask_prefix("/status") is None


def test_a_prefix_in_the_middle_does_not_count() -> None:
    assert strip_ask_prefix("please /ask someone else") is None


def test_empty_input_is_not_a_question() -> None:
    assert strip_ask_prefix("") is None
    assert strip_ask_prefix("   ") is None


# -- the command is offered on every surface -------------------------------

def test_the_chat_bridges_expose_ask() -> None:
    from argus.life.chat.router import COMMAND_MENU, help_text

    assert any(name == "ask" for name, _desc in COMMAND_MENU)
    assert "/ask" in help_text("Telegram")


def test_the_chat_router_routes_every_alias() -> None:
    from argus.life.chat.router import CommandRouter

    handlers = CommandRouter.dispatch.__doc__ or ""
    # Routing is a dict literal inside dispatch(); assert on the source so a
    # dropped alias fails here rather than silently becoming "unknown command".
    import inspect

    source = inspect.getsource(CommandRouter.dispatch)
    for alias in ASK_PREFIXES:
        assert f'"{alias}"' in source, alias
    assert handlers is not None


def test_the_shared_command_table_lists_ask() -> None:
    from pathlib import Path

    commands = (
        Path(__file__).resolve().parents[1]
        / "frontend" / "core" / "src" / "commands.ts"
    ).read_text(encoding="utf-8")

    assert "id: 'ask'" in commands
    # The description has to say what it does not do, or nobody reaches for it.
    assert "no task queued" in commands


@pytest.mark.parametrize("prefix", ASK_PREFIXES)
@pytest.mark.parametrize("cached_runner", [False, True])
@pytest.mark.parametrize(
    ("exit_code", "agent_messages", "fatal_error", "expected_reply"),
    [
        pytest.param(
            0, ["Checking the configuration.", "The configured backend is ready."], None,
            "The configured backend is ready.", id="answer",
        ),
        pytest.param(
            0, [], None,
            "Could not answer inline: The Manager returned an empty reply; nothing was queued.",
            id="empty",
        ),
        pytest.param(
            1, ["Incomplete answer."], None,
            "Could not answer inline: The Manager backend failed; nothing was queued.",
            id="failed",
        ),
        pytest.param(
            0, ["Incomplete answer."], "backend unavailable",
            "Could not answer inline: The Manager backend failed; nothing was queued.",
            id="fatal-error",
        ),
    ],
)
def test_the_web_bridge_answers_explicit_asks_without_task_dispatch(
    tmp_path, monkeypatch, prefix, cached_runner, exit_code, agent_messages,
    fatal_error, expected_reply,
) -> None:
    from types import SimpleNamespace

    from argus.apps import _runtime
    from argus.core.models import RunnerResult
    from argus.core.transcript import read_turns
    from argus.life.memory import LifeMemory, MemoryBundle
    from argus.manager import config_intent, front_door
    from argus.roles.prompts.manager import build_quick_reply_prompt
    from argus.webapi import manager_bridge, manager_state

    sid = "s-explicit-ask"
    life = tmp_path / "projects" / sid
    memory = LifeMemory.open(life)
    monkeypatch.setitem(manager_state._STATES, sid, {})
    question = "what backends are configured?"
    message = f"{prefix} {question}"
    calls = []
    intake_memory = []
    learned_replies = []
    monkeypatch.setattr(
        manager_bridge, "_schedule_answer_learning",
        lambda *args, **kwargs: learned_replies.append(kwargs["reply"]),
    )

    def classify(mem, body, state, **_kwargs):
        assert mem.project_root == life
        assert body == message
        assert state["session_id"] == sid
        calls.append("intake")
        intake_memory.append(mem)
        if cached_runner:
            assert front_door._ensure_manager_runner(state, mem) is runner
        # The shared intake may recommend TEAM work. The explicit prefix
        # still owns routing and must bypass the ordinary task pipeline.
        return None, None, "complex"

    def answer(*, prompt, options, run_label):
        assert run_label == "manager-ask"
        assert options.skip_git_repo_check is True
        assert options.force_safe_mode is True
        assert options.sandbox_mode == "read-only"
        assert options.working_dir == str(life)
        assert prompt.startswith(build_quick_reply_prompt(objective=question))
        calls.append("answer")
        return RunnerResult(
            exit_code=exit_code,
            agent_messages=agent_messages,
            fatal_error=fatal_error,
            stdout_lines=['{"type":"tool_execution","text":"internal trace"}'],
        )

    def unexpected_dispatch(*_args, **_kwargs):
        pytest.fail("explicit asks must not reach ordinary task classification or dispatch")

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    runner = SimpleNamespace(run_exec=answer)

    def build_runner(ns):
        assert isinstance(ns.manager_memory, MemoryBundle)
        assert ns.manager_memory is intake_memory[0]
        assert ns.global_root == str(tmp_path)
        assert ns.manager_session_root == str(life)
        assert ns.project_state_dir == str(life)
        calls.append("build")
        return runner

    monkeypatch.setattr(_runtime, "build_life_runner", build_runner)
    for name in (
        "_classify_operator_turn", "_run_triage_and_fallbacks", "_dispatch_team_mission",
    ):
        monkeypatch.setattr(manager_bridge, name, unexpected_dispatch)

    result = manager_bridge.manager_message(sid, message, global_root=tmp_path)

    succeeded = exit_code == 0 and bool(agent_messages) and not fatal_error
    assert result == (
        {"kind": "chat", "reply": expected_reply}
        if succeeded else
        {"kind": "error", "success": False, "error_code": "inline_reply_failed",
         "reply": expected_reply}
    )
    assert learned_replies == ([expected_reply] if succeeded else [])
    assert calls == ["intake", "build", "answer"]
    assert memory.backlog.all() == []
    assert [(turn["role"], turn["text"]) for turn in read_turns(life)] == [
        ("operator", message),
        ("argus", result["reply"]),
    ]


@pytest.mark.parametrize("failure", ["missing", "construction", "exception"])
def test_an_inline_answer_never_falls_through_to_dispatch(
    tmp_path, monkeypatch, failure,
) -> None:
    from argus.life.memory import LifeMemory
    from argus.manager import config_intent, front_door
    from argus.webapi import manager_bridge, manager_state

    sid = "s-unavailable-ask"
    memory = LifeMemory.open(tmp_path / "projects" / sid)
    monkeypatch.setitem(manager_state._STATES, sid, {})
    monkeypatch.setattr(
        config_intent, "_front_door_classify", lambda *a, **k: (None, None, "simple"),
    )

    def unavailable(state, mem):
        if failure == "exception":
            raise RuntimeError("backend down")
        if failure == "construction":
            state["manager_runner_error"] = "RuntimeError: backend down"
        return None

    def unexpected_dispatch(*args, **kwargs):
        pytest.fail("failed asks must not dispatch work or learn a successful answer")

    monkeypatch.setattr(front_door, "_ensure_manager_runner", unavailable)
    monkeypatch.setattr(manager_bridge, "_dispatch_team_mission", unexpected_dispatch)
    monkeypatch.setattr(manager_bridge, "_schedule_answer_learning", unexpected_dispatch)

    result = manager_bridge.manager_message(
        sid, "/ask why is the sky blue", global_root=tmp_path,
    )

    assert result["kind"] == "error"
    assert result["success"] is False
    assert result["error_code"] == "inline_reply_failed"
    assert "nothing was queued" in result["reply"]
    assert ("No conversational backend" if failure == "missing" else "backend down") in result["reply"]
    assert memory.backlog.all() == []


@pytest.mark.parametrize("channel", ["telegram", "feishu"])
@pytest.mark.parametrize("prefix", ASK_PREFIXES)
@pytest.mark.parametrize("failure", [False, True])
def test_chat_router_reuses_intake_memory_and_runner(
    tmp_path, monkeypatch, channel, prefix, failure,
) -> None:
    from types import SimpleNamespace

    from argus.apps import _runtime
    from argus.core.models import RunnerResult
    from argus.life.chat.router import CommandRouter
    from argus.life.memory import LifeMemory
    from argus.manager import config_intent, front_door

    life = tmp_path / "projects" / "s-router-ask"
    memory = LifeMemory.open(life)
    replies = []
    router = CommandRouter(
        life_dir=life,
        transport=SimpleNamespace(channel=channel, send=replies.append),
    )
    intake_memory = []
    builds = []

    def classify(mem, text, state, **kwargs):
        intake_memory.append(mem)
        assert state is router._state
        assert front_door._ensure_manager_runner(state, mem) is backend
        return None, None, "complex"

    def build_runner(ns):
        assert ns.manager_memory is intake_memory[0]
        assert ns.global_root == str(tmp_path)
        assert ns.manager_session_root == str(life)
        builds.append(ns)
        return backend

    def answer(*, prompt, options, run_label):
        assert run_label == "manager-ask"
        assert options.force_safe_mode and options.sandbox_mode == "read-only"
        if failure:
            raise RuntimeError("backend <offline>")
        return RunnerResult(exit_code=0, agent_messages=["Ready <now>"])

    backend = SimpleNamespace(run_exec=answer)
    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(_runtime, "build_life_runner", build_runner)
    monkeypatch.setattr(
        router, "_queue_task", lambda *a, **k: pytest.fail("Explicit asks must not queue work"),
    )

    router.dispatch(f"{prefix} what is running?")

    assert len(intake_memory) == len(builds) == len(replies) == 1
    if failure:
        assert "backend &lt;offline&gt;" in replies[0]
        assert "\u672a\u6392\u5165\u4efb\u4f55\u4efb\u52a1" in replies[0]
    else:
        assert replies == ["Ready &lt;now&gt;"]
    assert memory.backlog.all() == []
