"""A task's recorded trajectory and the relations between map cards.

The rows below follow the shapes of the survey project (a turn dispatching a
team task, Planner checks, plan replacements, a question about the delivered
survey) and of the c4 project (a chosen option handed to the team).
"""

import json

from fastapi.testclient import TestClient

from argus.webapi.map_relations import project_relations, relation_facts, reply_paths
from argus.webapi.map_trace import ENTRY_LIMIT_PER_CALL, OUTPUT_LIMIT, task_trace
from argus.webapi.map_view import turn_records

PROMPT = "SECRET PROMPT BODY never shown"


def _write(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _codex(call, ts, item):
    return {"call_id": call, "ts": ts, "stream": "stdout",
            "line": json.dumps({"type": "item.completed", "item": item})}


def _project(tmp_path):
    life = tmp_path / "life"
    (life / "handoffs" / "item-a" / "role-sessions").mkdir(parents=True)
    (life / "handoffs" / "item-a" / "role-sessions" / "engineer.json").write_text(
        json.dumps({"role": "engineer", "thread_id": "thread-eng", "seen_thread_ids": ["thread-eng"]}))
    _write(life / "events.jsonl", [
        {"type": "round.start", "item_id": "item-a", "round": 1, "ts": 1},
        {"type": "agent.io.start", "call_id": "eng-1", "run_label": "engineer-r1", "ts": 2},
        {"type": "round.start", "item_id": "item-b", "round": 1, "ts": 3},
        {"type": "agent.io.start", "call_id": "eng-b", "run_label": "engineer-r1", "ts": 4},
        {"type": "round.review.started", "item_id": "item-a", "ts": 5},
        {"type": "agent.io.start", "call_id": "rev-1", "run_label": "reviewer", "ts": 6},
        {"type": "life.mission.completed", "item_id": "item-a", "status": "done", "ts": 7,
         "summary": "## 综述已交付并通过独立复核\n细节……"},
    ])
    _write(life / "agent_io.jsonl", [
        {"type": "agent.io.start", "call_id": "eng-1", "run_label": "engineer-r1", "model": "m",
         "prompt": PROMPT, "ts": 2, "working_dir": "/work"},
        {"call_id": "eng-1", "ts": 2.1, "stream": "stdout",
         "line": json.dumps({"type": "thread.started", "thread_id": "thread-eng"})},
        _codex("eng-1", 2.2, {"type": "reasoning", "text": "**Planning** read the plan first"}),
        _codex("eng-1", 2.3, {"type": "command_execution", "command": "cat plan.md",
                              "aggregated_output": "x" * 5000 + "END", "exit_code": 0, "status": "completed"}),
        _codex("eng-1", 2.4, {"type": "file_change", "changes": [{"path": "/work/survey.md", "kind": "add"}]}),
        _codex("eng-1", 2.5, {"type": "mcp_tool_call", "server": "argus", "tool": "progress", "status": "completed"}),
        _codex("eng-1", 2.6, {"type": "agent_message", "text": "Wrote the survey."}),
        {"type": "agent.io.start", "call_id": "eng-b", "run_label": "engineer-r1", "model": "m",
         "prompt": PROMPT, "ts": 4},
        _codex("eng-b", 4.1, {"type": "agent_message", "text": "other item's words"}),
        {"type": "agent.io.start", "call_id": "rev-1", "run_label": "reviewer", "model": "m",
         "prompt": PROMPT, "ts": 6},
        *[_codex("rev-1", 6 + i / 1000, {"type": "agent_message", "text": f"check {i}"})
          for i in range(ENTRY_LIMIT_PER_CALL + 5)],
    ])
    return life


def test_trace_groups_by_role_and_round_in_time_order_with_caps_and_no_prompt(tmp_path):
    trace = task_trace(_project(tmp_path), "item-a")

    assert trace["conclusion"] == "综述已交付并通过独立复核"
    assert [(g["role"], g["round"]) for g in trace["groups"]] == [("engineer", 1), ("reviewer", 1)]
    engineer, reviewer = trace["groups"]
    assert [e["kind"] for e in engineer["entries"]] == ["reasoning", "command", "file_change", "tool", "message"]
    command = engineer["entries"][1]
    assert command["output"].endswith("END") and len(command["output"]) <= OUTPUT_LIMIT
    assert engineer["entries"][2]["paths"] == [{"path": "survey.md", "change": "add"}]
    assert len(reviewer["entries"]) == ENTRY_LIMIT_PER_CALL and reviewer["truncated"] and trace["truncated"]
    body = json.dumps(trace, ensure_ascii=False)
    assert PROMPT not in body and "other item's words" not in body


def test_trace_route_requires_auth(tmp_path):
    from argus.webapi.server import create_app

    client = TestClient(create_app(global_root=tmp_path, auth_token="tok"))
    assert client.get("/api/projects/s-x/map-trace", params={"task": "item-a"}).status_code == 401


def _survey_rows():
    reply = "你要的中文综述已经写好：[大模型强化学习中的奖励作弊综述](reward-hacking-survey-zh.md)"
    return [
        {"type": "ui.operator", "message_id": "web-1-operator", "text": "调研奖励作弊", "ts": 1},
        {"type": "manager.turn.started", "message_id": "web-1", "text": "调研奖励作弊", "ts": 1},
        {"type": "agent.io.start", "call_id": "c1", "run_label": "simple-1", "ts": 2},
        {"type": "agent.io.complete", "call_id": "c1", "run_label": "simple-1", "exit_code": 0, "ts": 3},
        {"type": "life.planner.task_added", "item_id": "survey", "source": "manager_operator", "ts": 4},
        {"type": "life.manager.intent.completed", "item_id": "survey", "route": "team", "ts": 4},
        {"type": "life.mission.completed", "item_id": "survey", "status": "done", "ts": 10,
         "delivery_candidates": ["reward-hacking-survey-zh.md"]},
        {"type": "life.planner.start", "cycle": 1, "ts": 11},
        {"type": "life.planner.verdict", "status": "planned", "mission_id": "survey", "ts": 12},
        {"type": "life.planner.task_added", "item_id": "check", "ts": 13},
        {"type": "life.mission.completed", "item_id": "check", "status": "replan_requested", "ts": 14},
        {"type": "life.planner.start", "cycle": 2, "ts": 15},
        {"type": "life.planner.task_added", "item_id": "recheck", "ts": 16},
        {"type": "life.planner.verdict", "status": "planned", "mission_id": "check", "ts": 16.5},
        {"type": "ui.operator", "message_id": "web-2-operator", "text": "为啥我看不懂你在干啥", "ts": 20},
        {"type": "manager.turn.started", "message_id": "web-2", "text": "为啥我看不懂你在干啥", "ts": 20},
        {"type": "ui.argus", "message_id": "web-2-argus", "text": reply, "task_turn": True,
         "mission_result": True, "success": True, "ts": 21},
    ]


def _survey_tasks(turns):
    tasks = [
        {"id": "survey", "status": "done", "ts": 4, "owns_paths": [], "plan_id": ""},
        {"id": "check", "status": "superseded", "ts": 13, "plan_id": "plan-1", "superseded_by_plan_id": "plan-2",
         "owns_paths": ["reward-hacking-survey-zh.md"]},
        {"id": "recheck", "status": "running", "ts": 16, "plan_id": "plan-2",
         "owns_paths": ["reward-hacking-survey-zh.md"]},
    ]
    return tasks + [dict(turn["card"]) for turn in turns.values()]


def test_survey_relations_link_question_check_replacement_and_dispatch():
    rows = _survey_rows()
    turns = turn_records(rows)
    tasks = project_relations(_survey_tasks(turns), turns, relation_facts(rows, {}))
    relations = {task["id"]: task["relations"] for task in tasks}

    # The turn that handed its request to the team finished; it is not running.
    assert turns["turn:web-1"]["card"]["status"] == "done"
    assert relations["survey"] == [{"kind": "carries_out", "target": "turn:web-1"}]
    assert relations["check"] == [{"kind": "checks", "target": "survey"}]
    # Replacement says the most about this pair, so the follow-up is not repeated.
    assert relations["recheck"] == [{"kind": "replaces", "target": "check"}]
    # The question names the delivered survey file: it is about the survey card,
    # not the live item that merely owns the same path.
    assert relations["turn:web-2"] == [{"kind": "about", "target": "survey"}]


def test_c4_chosen_option_turn_finishes_when_the_team_task_starts():
    rows = [
        {"type": "manager.turn.started", "message_id": "web-9", "text": "2", "ts": 1},
        {"type": "life.planner.task_added", "item_id": "cli", "source": "manager_direct", "ts": 2},
        {"type": "life.manager.intent.completed", "item_id": "cli", "route": "team", "ts": 2},
        {"type": "life.mission.completed", "item_id": "cli", "status": "done", "ts": 9,
         "delivery_candidates": ["moneyfmt/csv_report.py"]},
        {"type": "life.planner.verdict", "status": "completed", "mission_id": "cli", "ts": 10},
    ]
    turns = turn_records(rows)
    tasks = project_relations([{"id": "cli", "status": "done", "ts": 2}, dict(turns["turn:web-9"]["card"])],
                              turns, relation_facts(rows, {}))
    assert turns["turn:web-9"]["card"]["status"] == "done"
    assert {t["id"]: t["relations"] for t in tasks} == {
        "cli": [{"kind": "carries_out", "target": "turn:web-9"}], "turn:web-9": [],
    }


def test_reply_paths_keep_workspace_files_and_drop_links_out():
    text = "见 [综述](reward-hacking-survey-zh.md)、`/work/notes/a.md` 和 [论文](https://arxiv.org/abs/1)"
    assert reply_paths(text, "/work") == ["reward-hacking-survey-zh.md", "notes/a.md"]
