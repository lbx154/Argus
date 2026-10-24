"""A turn card is named after the operator's own words, not what they quoted.

``map_references.expand_operator_references`` replaces every quoted-card marker
with a readable inline line (``（引用：《…》）`` / ``(Referenced: "…")``) *before*
the message is persisted, so the first line of the ask is often a quote, not
the question. The card title must skip those lines (Atlas Q&A 06 on the
stable web trial showed a garbled quote as its title, 2026-09-16).
"""

from __future__ import annotations

from argus.webapi.map_view import ask_title, turn_records


def _card(text: str, *, answered: bool = True) -> dict:
    rows = [
        {"type": "ui.operator", "message_id": "web-q-operator", "ts": 10, "text": text},
        {"type": "manager.turn.started", "message_id": "web-q", "ts": 11, "text": text, "turn_kind": "qa"},
    ]
    if answered:
        # The reply row rebuilds the card (title included) from the ask.
        rows.append({"type": "ui.argus", "message_id": "web-q-argus", "ts": 12, "text": "Answer.",
                     "steps": [{"label": "answer", "started_ts": 11, "ended_ts": 12}]})
    return turn_records(rows, {}, {})["turn:web-q"]["card"]


def test_title_falls_back_to_the_quote_when_nothing_else_was_said() -> None:
    text = "（引用：《Coverage study》）"
    assert ask_title(text) == text
    assert _card(text)["title"] == text


def test_a_choice_card_reply_is_titled_by_the_task_it_starts() -> None:
    """Picking "直接做" on the intake card starts the request made earlier; the
    card is about that request (stable web trial s-54218bf4, 2026-09-18)."""
    request = "你学习一下FA 就是初创公司融资的相关知识"
    rows = [
        {"type": "ui.operator", "message_id": "web-q-operator", "ts": 10, "text": "直接做"},
        {"type": "manager.turn.started", "message_id": "web-q", "ts": 11, "text": request},
    ]
    turns: dict = {}
    asks: dict = {}
    running = turn_records(rows, turns, asks)["turn:web-q"]["card"]
    assert running["title"] == request
    # The reply arrives on a later incremental read and rebuilds the card.
    reply = [{"type": "ui.argus", "message_id": "web-q-argus", "ts": 12, "text": "FA 是……",
              "task_turn": True, "success": True}]
    card = turn_records(reply, turns, asks)["turn:web-q"]["card"]
    assert card["title"] == request
    assert card["objective"] == request
