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
@pytest.mark.parametrize(
    ("exit_code", "agent_messages", "fatal_error", "expected_reply"),
    [
        pytest.param(
            0, ["Checking the configuration.", "The configured backend is ready."], None,
            "The configured backend is ready.", id="answer",
        ),
        pytest.param(
            0, [], None,
            "The Manager returned an empty reply; nothing was queued.", id="empty",
        ),
        pytest.param(
            1, ["Incomplete answer."], None,
            "Could not answer inline just now; nothing was queued.", id="failed",
        ),
        pytest.param(
            0, ["Incomplete answer."], "backend unavailable",
            "Could not answer inline just now; nothing was queued.", id="fatal-error",
        ),
    ],
)
def test_the_web_bridge_answers_explicit_asks_without_task_dispatch(
    tmp_path, monkeypatch, prefix, exit_code, agent_messages, fatal_error, expected_reply,
) -> None:
    from types import SimpleNamespace

    from argus.core.models import RunnerResult
    from argus.core.transcript import read_turns
    from argus.life.memory import LifeMemory
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

    def classify(mem, body, state, **_kwargs):
        assert mem.project_root == life
        assert body == message
        assert state["session_id"] == sid
        calls.append("intake")
        # The shared intake may recommend TEAM work. The explicit prefix
        # still owns routing and must bypass the ordinary task pipeline.
        return None, None, "complex"

    def answer(*, prompt, options, run_label):
        assert run_label == "manager-ask"
        assert options.skip_git_repo_check is True
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
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a, **_k: runner)
    for name in (
        "_classify_operator_turn", "_run_triage_and_fallbacks", "_dispatch_team_mission",
    ):
        monkeypatch.setattr(manager_bridge, name, unexpected_dispatch)

    result = manager_bridge.manager_message(sid, message, global_root=tmp_path)

    assert result == {"kind": "chat", "reply": expected_reply}
    assert calls == ["intake", "answer"]
    assert memory.backlog.all() == []
    assert [(turn["role"], turn["text"]) for turn in read_turns(life)] == [
        ("operator", message),
        ("argus", result["reply"]),
    ]


def test_an_inline_answer_never_falls_through_to_dispatch(tmp_path, monkeypatch) -> None:
    from argus.manager import front_door
    from argus.webapi import manager_bridge

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("backend down")),
    )

    reply = manager_bridge._answer_inline("s-1", tmp_path, "why is the sky blue")

    # A failure returns prose, not an exception and not a queued task. Both
    # failure branches say so explicitly.
    assert isinstance(reply, str) and reply
    assert "queue" in reply
