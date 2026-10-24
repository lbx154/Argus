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

from argus.manager.ask_intent import ASK_PREFIXES, strip_ask_prefix

# -- recognising the intent -------------------------------------------------


def test_surrounding_whitespace_is_ignored() -> None:
    assert strip_ask_prefix("   /ask   spaced out   ") == "spaced out"


# -- what must NOT be treated as a question --------------------------------


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
