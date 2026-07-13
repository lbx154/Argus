"""UX-D / T10: the chat fast-path shows only the reply, not mission scaffolding.

A greeting used to render "🔧 round 1: main agent finished\n   ↳ <reply>"; now
it reads like a chat reply (see ``manager_triage``'s ``_Capture`` sink, which
does this same job in the ACTUAL production chat fast-path — swallow
loop.start/engineer.progress scaffolding, surface only ``round.main.completed``
text). ``_extract_chat_reply_text`` is the shared plain/JSON-reply parser both
that sink and this test file's own (now-removed) prototype sink used.
"""
from __future__ import annotations

from argus_skill.manager.front_door import _extract_chat_reply_text


def test_extract_chat_reply_text_plain_and_json():
    assert _extract_chat_reply_text("你好，我在") == "你好，我在"
    assert _extract_chat_reply_text('{"reply": "hi there"}') == "hi there"
    assert _extract_chat_reply_text('{"message": "yo"}') == "yo"
    # garbage / non-reply JSON falls back to the raw text
    assert _extract_chat_reply_text('{"x": 1}') == '{"x": 1}'
    assert _extract_chat_reply_text("") == ""
