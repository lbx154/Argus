"""UX-D / T10: the chat fast-path shows only the reply, not mission scaffolding.

A greeting used to render "🔧 round 1: main agent finished\n   ↳ <reply>"; now
it reads like a chat reply.
"""
from __future__ import annotations

from argus_skill.manager.repl import _ChatReplySink, _extract_chat_reply_text


def test_extract_chat_reply_text_plain_and_json():
    assert _extract_chat_reply_text("你好，我在") == "你好，我在"
    assert _extract_chat_reply_text('{"reply": "hi there"}') == "hi there"
    assert _extract_chat_reply_text('{"message": "yo"}') == "yo"
    # garbage / non-reply JSON falls back to the raw text
    assert _extract_chat_reply_text('{"x": 1}') == '{"x": 1}'
    assert _extract_chat_reply_text("") == ""


def test_chat_sink_prints_only_reply(capsys):
    sink = _ChatReplySink(theme=None)
    # scaffolding events are swallowed
    sink.handle_event({"type": "loop.start", "text": "chat: hi"})
    sink.handle_event({"type": "engineer.progress", "text": "thinking..."})
    sink.handle_event({"type": "loop.done", "text": "done"})
    assert sink.replied is False
    assert capsys.readouterr().out == ""
    # the reply event prints just the reply
    sink.handle_event({"type": "round.main.completed", "last_message": "你好，我在"})
    out = capsys.readouterr().out
    assert sink.replied is True
    assert "你好，我在" in out
    assert "main agent finished" not in out
    assert "🔧" not in out


def test_chat_sink_empty_reply_does_not_mark_replied(capsys):
    sink = _ChatReplySink(theme=None)
    sink.handle_event({"type": "round.main.completed", "last_message": ""})
    assert sink.replied is False
    assert capsys.readouterr().out == ""
