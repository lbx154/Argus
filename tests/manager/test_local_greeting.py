"""A bare greeting is answered locally; anything with content still reaches the classifier."""
from __future__ import annotations

import pytest

from argus.manager.greeting import greeting_reply


@pytest.mark.parametrize("text", ["你好", "你好！", " 您好 ", "Hi", "hello!!", "hey 👋", "早上好～", "在吗？"])
def test_bare_greetings_get_a_local_reply(text: str) -> None:
    reply = greeting_reply(text)
    assert reply
    assert ("你好" in reply) == any("㐀" <= ch <= "鿿" for ch in text)


@pytest.mark.parametrize("text", ["你好，帮我调研一下 Jev", "hi, what is the status?", "好的", "ok", "继续", "停", "", "greeting"])
def test_messages_with_content_or_consent_are_not_greetings(text: str) -> None:
    assert greeting_reply(text) == ""


def test_front_door_answers_a_bare_greeting_without_the_classifier() -> None:
    from argus.manager.config_intent import _front_door_classify

    def never(_chat_state, _mem):
        raise AssertionError("a bare greeting must not build the Manager runner")

    chat_state: dict = {}
    assert _front_door_classify(None, "你好！", chat_state, ensure_runner=never) == (None, None, "simple")
    assert chat_state["_frontdoor_greeting_reply"].startswith("你好")
    assert chat_state["_frontdoor_self_mode"] == "reply"


def test_front_door_still_classifies_a_greeting_with_a_request() -> None:
    from argus.manager.config_intent import _front_door_classify

    chat_state: dict = {}
    # No runner available: the classifier path reports its failure instead of answering.
    result = _front_door_classify(None, "你好，帮我调研一下 Jev", chat_state, ensure_runner=lambda *_: None)
    assert result == (None, None, "complex")
    assert "_frontdoor_greeting_reply" not in chat_state
