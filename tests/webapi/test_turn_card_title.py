"""A turn card is named after the operator's own words, not what they quoted.

``map_references.expand_operator_references`` replaces every quoted-card marker
with a readable inline line (``（引用：《…》）`` / ``(Referenced: "…")``) *before*
the message is persisted, so the first line of the ask is often a quote, not
the question. The card title must skip those lines (Atlas Q&A 06 on the
stable web trial showed a garbled quote as its title, 2026-09-16).
"""

from __future__ import annotations

from argus.webapi.map_view import ask_title, turn_records


def _card(text: str) -> dict:
    rows = [
        {"type": "ui.operator", "message_id": "web-q-operator", "ts": 10, "text": text},
        {"type": "manager.turn.started", "message_id": "web-q", "ts": 11, "text": text, "turn_kind": "qa"},
    ]
    return turn_records(rows, {}, {})["turn:web-q"]["card"]


def test_title_skips_leading_zh_reference_lines() -> None:
    text = (
        "（引用：《你真的上网调研了吗？》）\n"
        "（引用：《你赶紧上网调研一下 agentic RL大家都在怎么做》）\n"
        "这两个回答有啥区别"
    )
    card = _card(text)
    assert card["title"] == "这两个回答有啥区别"
    # The full ask, quotes included, stays available as the objective.
    assert card["objective"] == text


def test_title_skips_en_reference_with_step_on_the_same_line() -> None:
    text = '(Referenced: "Coverage study" · Sampling plan) What changed since then?'
    assert ask_title(text) == "What changed since then?"


def test_title_falls_back_to_the_quote_when_nothing_else_was_said() -> None:
    text = "（引用：《Coverage study》）"
    assert ask_title(text) == text
    assert _card(text)["title"] == text


def test_plain_first_line_is_unchanged() -> None:
    assert ask_title("Write a short story\nabout a lighthouse") == "Write a short story"
    assert ask_title("") == ""
