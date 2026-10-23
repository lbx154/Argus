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


