"""The map shows the work, not the chat (design/role-authority.md "Map: one
picture of the work"): a request is a chip on the task it started, a chat-only
answer is not on the map, a replaced task folds into its replacement, and a
title never shows a canvas card id."""

import json

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi.map_relations import fold_cards, project_relations, relation_facts
from argus.webapi.map_view import plain_card_ids, read_map, turn_records
from tests.webapi.test_map_trace_relations import _survey_rows, _survey_tasks


def _project(tmp_path, rows, items=(), canvas=None):
    sid = "s-one-picture"
    write_session_meta(tmp_path, SessionMeta(id=sid, created=1, last_active=1, display_name="One picture"))
    life = tmp_path / "projects" / sid
    memory = LifeMemory.open(life)
    for item in items:
        memory.backlog.add(item)
    (life / "events.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    if canvas is not None:
        (life / "canvas.json").write_text(json.dumps(canvas, ensure_ascii=False))
    return read_map(sid, tmp_path, life)


def _turn(n, asked, ts, **reply):
    return [
        {"type": "ui.operator", "message_id": f"web-{n}-operator", "text": asked, "ts": ts},
        {"type": "manager.turn.started", "message_id": f"web-{n}", "text": asked, "ts": ts,
         **({"turn_kind": reply.pop("turn_kind")} if "turn_kind" in reply else {})},
        *([{"type": "ui.argus", "message_id": f"web-{n}-argus", "ts": ts + 1, **reply}] if reply else []),
    ]


def test_a_request_is_a_chip_on_the_task_it_started_and_a_chat_answer_is_not_on_the_map(tmp_path):
    rows = [
        *_turn(1, "帮我做一个小工具，统计词频", 1),
        {"type": "life.manager.intent.completed", "item_id": "tool", "route": "team", "ts": 2},
        {"type": "ui.argus", "message_id": "web-1-argus", "text": "交给团队了。", "task_turn": True,
         "mission_result": True, "success": True, "ts": 2.5},
        # A question answered in the chat: no task, no card, no chip.
        *_turn(2, "这个工具用什么语言？", 5, text="Python。", turn_kind="qa", success=True),
        # Small work done in the conversation itself stays in the conversation.
        *_turn(3, "README 有几行？", 7, text="一行。", success=True, steps=[
            {"kind": "command_execution", "label": "$ wc -l README.md", "status": "completed",
             "started_ts": 7.2, "ended_ts": 7.4}]),
        # Still being read: it may yet start a task, so it waits by the composer.
        *_turn(4, "更正：输出按次数从高到低", 9),
    ]
    data = _project(tmp_path, rows, [BacklogItem(id="tool", ts=2, title="词频小工具", objective="统计词频",
                                                 status="running")])
    assert [task["id"] for task in data["tasks"]] == ["tool"]
    (tool,) = data["tasks"]
    assert [(r["id"], r["text"]) for r in tool["requests"]] == [("turn:web-1", "帮我做一个小工具，统计词频")]
    assert all(r["target"] != "turn:web-1" for r in tool["relations"])
    assert [(r["id"], r["text"], r["status"]) for r in data["requests"]] == [
        ("turn:web-4", "更正：输出按次数从高到低", "running"),
    ]


def test_continuing_after_a_checkpoint_rides_on_the_question_it_answered(tmp_path):
    rows = [
        *_turn(1, "继续，按你推荐的来。", 10),
        {"type": "life.operator_question.answered", "item_id": "step-2-check", "decided_by": "operator",
         "option": "继续", "ts": 11},
        {"type": "ui.argus", "message_id": "web-1-argus", "text": "好的，继续第 3 步。", "task_turn": True,
         "success": True, "ts": 12},
    ]
    data = _project(tmp_path, rows, [BacklogItem(id="step-2-check", ts=9, title="第 2 步完成了",
                                                 objective="看一下结果", status="done")])
    (card,) = data["tasks"]
    assert [r["text"] for r in card["requests"]] == ["继续，按你推荐的来。"]
    assert data["requests"] == []


def test_a_replaced_task_folds_into_its_replacement_with_its_history():
    rows = _survey_rows()
    turns = turn_records(rows)
    tasks = _survey_tasks(turns)
    tasks[1].update(title="复核综述", superseded_reason="新计划改成\n  逐条核对引用")
    tasks.append({"id": "later", "status": "pending", "ts": 30, "deps": ["check", "recheck"]})
    kept, loose = fold_cards(project_relations(tasks, turns, relation_facts(rows, {})))
    by_id = {task["id"]: task for task in kept}
    # Two cards of work; the request rides on the survey; the replaced check is inside its replacement.
    assert sorted(by_id) == ["later", "recheck", "survey"] and loose == []
    assert [r["text"] for r in by_id["survey"]["requests"]] == ["调研奖励作弊"]
    recheck = by_id["recheck"]
    assert recheck["replaced"] == [{"id": "check", "title": "复核综述", "reason": "新计划改成 逐条核对引用", "ts": 13}]
    assert recheck["replaced_count"] == 1
    # What the old task was to other cards carries over; what pointed at it points at the replacement.
    assert recheck["relations"] == [{"kind": "checks", "target": "survey"}]
    assert by_id["later"]["deps"] == ["recheck"]


def test_a_replacement_chain_folds_into_the_last_task_and_keeps_requests():
    tasks = [
        {"id": "a", "status": "superseded", "ts": 1, "title": "方案 A", "superseded_reason": "太慢",
         "relations": [{"kind": "carries_out", "target": "turn:web-1"}]},
        {"id": "b", "status": "superseded", "ts": 2, "title": "方案 B", "relations": [{"kind": "replaces", "target": "a"}]},
        {"id": "c", "status": "running", "ts": 3, "title": "方案 C", "relations": [{"kind": "replaces", "target": "b"}]},
        {"id": "turn:web-1", "kind": "turn", "status": "done", "ts": 0, "title": "试试 A", "objective": "试试 A"},
    ]
    kept, _ = fold_cards(tasks)
    (c,) = kept
    assert [r["title"] for r in c["replaced"]] == ["方案 A", "方案 B"] and c["replaced_count"] == 2
    assert [r["text"] for r in c["requests"]] == ["试试 A"]
    assert c["relations"] == []


def test_a_finished_task_a_new_plan_retired_stays_a_card():
    tasks = [
        {"id": "done", "status": "done", "ts": 1, "superseded_by_plan_id": "p2"},
        {"id": "next", "status": "pending", "ts": 2, "plan_id": "p2", "relations": [{"kind": "replaces", "target": "done"}]},
    ]
    kept, _ = fold_cards(tasks)
    assert [task["id"] for task in kept] == ["done", "next"] and "replaced" not in kept[1]


def test_titles_say_a_step_by_its_number_and_words_never_its_card_id(tmp_path):
    canvas = {
        "version": 1, "revision": 3, "positions": {},
        "cards": [
            {"id": "hc-35885033b637", "kind": "step", "text": "写 wordstat/core.py：count_words 返回词频字典", "created_ts": 1},
            {"id": "hc-07f8f8913e0f", "kind": "step", "text": "加命令行", "created_ts": 2},
            {"id": "hc-aaaabbbbcccc", "kind": "idea", "text": "用 vllm 做 rollout", "created_ts": 3},
        ],
        "links": [{"id": "l1", "source": "hc-35885033b637", "target": "hc-07f8f8913e0f", "kind": "order", "created_ts": 4}],
    }
    data = _project(tmp_path, [], [
        BacklogItem(id="t1", ts=1, title="完成必经步骤① hc-35885033b637", objective="按 hc-07f8f8913e0f 做",
                    status="done"),
        BacklogItem(id="t2", ts=2, title="清理 hc-deadbeef0000 留下的文件", objective="x", status="pending"),
    ], canvas)
    titles = {task["id"]: (task["title"], task["objective"]) for task in data["tasks"]}
    assert titles["t1"] == ("完成必经步骤①“写 wordstat/core.py…”", "按②“加命令行” 做")
    assert titles["t2"][0] == "清理 留下的文件"
    assert "hc-" not in json.dumps(titles, ensure_ascii=False)
    names = {"hc-aaaabbbbcccc": ("", "用 vllm 做 rollout")}
    assert plain_card_ids("来自hc-aaaabbbbcccc的想法", names) == "来自“用 vllm 做 rollout”的想法"
    assert plain_card_ids("no ids here", names) == "no ids here"
