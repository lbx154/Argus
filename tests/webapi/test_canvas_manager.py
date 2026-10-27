"""Canvas step 2: the Manager reads the canvas; cards are handed on and asked about.

- Each Manager turn sees what a person placed (capped), as context, and nothing
  when the canvas is empty.
- "交给团队" is an ordinary message quoting the card; the task the Manager
  hands to the team from it links back to the card (``from``).
- "问这张卡" quotes the card; the answering turn is ``about`` it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from argus.manager import front_door
from argus.webapi import canvas as store
from argus.webapi import manager_bridge, manager_state
from argus.webapi.map_relations import project_relations, relation_facts
from argus.webapi.map_view import turn_records
from tests.hand_off import replying

pytest.importorskip("fastapi")


def _project(root: Path, sid: str) -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(
        json.dumps({"type": "mission.started", "text": "hi", "ts": time.time()}) + "\n", encoding="utf-8")
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    return life


def _marker(card_id: str, title: str) -> str:
    ref = {"source": "live:s", "task_id": card_id, "task_title": title, "event_ids": []}
    return "[[Argus引用 " + json.dumps(ref, ensure_ascii=False) + "]]"


def _send(monkeypatch, tmp_path: Path, sid: str, text: str) -> str:
    manager_state._STATES.clear()
    seen: dict[str, str] = {}

    def triage(mem, body, chat_state, **kwargs):
        seen["body"] = body
        return "好的。"

    monkeypatch.setattr(front_door, "manager_turn", replying(triage))
    result = manager_bridge.manager_message(sid, text, global_root=tmp_path)
    assert result == {"kind": "chat", "reply": "好的。"}
    return seen["body"]


def test_manager_turn_reads_the_canvas_only_when_a_person_placed_something(tmp_path, monkeypatch):
    sid = "s-canvas-read"
    life = _project(tmp_path, sid)
    assert _send(monkeypatch, tmp_path, sid, "普通问题") == "普通问题"

    canvas, idea = store.add_card(life, 0, kind="idea", text="基线要用 3 个种子")
    canvas, _ = store.add_card(life, 1, kind="file", text="", attachment={
        "attachment_id": "a1", "name": "notes.pdf", "relative_path": "uploads/notes.pdf", "mime": "application/pdf"})
    canvas, _ = store.add_link(life, 2, source=idea["id"], target="task-x", kind="basis")
    body = _send(monkeypatch, tmp_path, sid, "接下来做什么？")
    assert body.index("接下来做什么？") < body.index(store.CONTEXT_HEADER)
    block = body[body.index(store.CONTEXT_HEADER):]
    assert "不是指令" in block
    assert f"想法卡 {idea['id']}: 基线要用 3 个种子" in block
    assert "uploads/notes.pdf" in block
    assert "连线（依据）: 想法卡《基线要用 3 个种子》 → 卡片 task-x" in block
    # The journaled operator turn is what was typed, not the context.
    transcript = (life / "transcript.jsonl").read_text(encoding="utf-8")
    assert store.CONTEXT_HEADER not in transcript


def test_canvas_context_is_capped_and_keeps_linked_and_recent_cards(tmp_path):
    revision = 0
    ids = []
    for index in range(60):
        _, card = store.add_card(tmp_path, revision, kind="idea", text=f"想法 {index} " + "长" * 80)
        ids.append(card["id"])
        revision += 1
    store.add_link(tmp_path, revision, source=ids[0], target=ids[1], kind="related")
    block = store.canvas_context_block(tmp_path)
    assert len(block.encode("utf-8")) <= store.DIGEST_MAX_BYTES
    lines = block.splitlines()
    # Linked cards first, then the newest; the oldest unlinked ones drop out.
    assert lines[1].startswith(f"- 想法卡 {ids[1]}") and lines[2].startswith(f"- 想法卡 {ids[0]}")
    assert lines[3].startswith(f"- 想法卡 {ids[59]}")
    assert f"- 想法卡 {ids[2]}:" not in block
    assert store.canvas_context_block(tmp_path / "empty") == ""


def test_hand_to_team_message_quotes_the_card_and_journals_it(tmp_path, monkeypatch):
    sid = "s-canvas-hand"
    life = _project(tmp_path, sid)
    _, idea = store.add_card(life, 0, kind="idea", text="写一个 CSV 导出")
    body = _send(monkeypatch, tmp_path, sid, _marker(idea["id"], "写一个 CSV 导出") + "\n把这张卡做成任务：写一个 CSV 导出")
    # The Manager is told which card, and what it says.
    assert "（引用：《写一个 CSV 导出》）" in body
    assert f"- 想法卡 {idea['id']}: 写一个 CSV 导出" in body
    rows = [json.loads(line) for line in (life / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    operator = [row for row in rows if row.get("type") == "ui.operator"]
    assert operator[-1]["refs"] == [idea["id"]]
    assert "[[Argus引用" not in operator[-1]["text"]


def test_task_handed_on_from_a_card_links_back_and_a_question_is_about_its_card():
    rows = [
        {"type": "ui.operator", "message_id": "web-1-operator", "text": "把这张卡做成任务：写导出",
         "refs": ["hc-idea"], "ts": 1},
        {"type": "manager.turn.started", "message_id": "web-1", "text": "把这张卡做成任务：写导出", "ts": 1},
        {"type": "life.planner.task_added", "item_id": "export", "source": "manager_operator", "ts": 2},
        {"type": "life.manager.intent.completed", "item_id": "export", "route": "team", "ts": 2},
        {"type": "ui.operator", "message_id": "web-2-operator", "text": "（引用：《写导出》）进展如何？",
         "refs": ["export"], "ts": 5},
        {"type": "manager.turn.started", "message_id": "web-2", "text": "进展如何？", "ts": 5, "turn_kind": "qa"},
        {"type": "ui.argus", "message_id": "web-2-argus", "text": "还在写。", "turn_kind": "qa", "ts": 6},
        {"type": "ui.operator", "message_id": "web-3-operator", "text": "这个想法靠谱吗", "refs": ["hc-idea"], "ts": 7},
        {"type": "manager.turn.started", "message_id": "web-3", "text": "这个想法靠谱吗", "ts": 7, "turn_kind": "qa"},
        {"type": "ui.argus", "message_id": "web-3-argus", "text": "靠谱。", "turn_kind": "qa", "ts": 8},
    ]
    turns = turn_records(rows)
    tasks = [{"id": "export", "status": "running", "ts": 2}, *(dict(turn["card"]) for turn in turns.values())]
    relations = {t["id"]: t["relations"] for t in project_relations(tasks, turns, relation_facts(rows, {}))}
    assert relations["export"] == [{"kind": "from", "target": "hc-idea"}, {"kind": "carries_out", "target": "turn:web-1"}]
    assert relations["turn:web-2"] == [{"kind": "about", "target": "export"}]
    assert relations["turn:web-3"] == [{"kind": "about", "target": "hc-idea"}]
