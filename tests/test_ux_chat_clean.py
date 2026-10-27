"""Replay of s-ec08e4a5 (trial-1023), call 7466732e: a Manager chat reply is shown as written.

The chat once unwrapped a reply after a "📢" marker or out of a JSON object
and hid live text starting with "{". No prompt asks for either shape, and none
of the 130 Manager chat messages in trial-1023 used one. The fixture keeps the
operator's question, both messages the Manager streamed, and the reply the
transcript showed.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.life.memory import MemoryBundle
from argus.manager import front_door
from tests.hand_off import manager_runner

CHAT = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "s_ec08e4a5" / "manager_chat.json").read_text(encoding="utf-8")
)


def _stream(turn) -> None:
    for index, text in enumerate(CHAT["messages"]):
        turn["sink"].handle_event({
            "type": "engineer.progress", "kind": "agent_message", "text": text, "message_id": f"m{index}",
        })


def test_the_manager_reply_is_shown_as_written(tmp_path: Path, monkeypatch) -> None:
    runner = manager_runner(hand=None, reply=CHAT["messages"][-1], during=_stream)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: runner)
    deltas: list[str] = []

    reply, handoff = front_door.manager_turn(
        MemoryBundle.for_cwd(tmp_path, global_root=tmp_path, fingerprint="s-ec08e4a5"), CHAT["operator"], {},
        on_fragment=lambda kind, payload: deltas.append(payload["text"]) if kind == "delta" else None,
    )

    assert reply == CHAT["shown"]
    assert handoff is None
    assert deltas == [text.strip() for text in CHAT["messages"]] + [CHAT["shown"]]
