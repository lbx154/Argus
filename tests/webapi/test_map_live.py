import json

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi import map_narrative as copy
from argus.webapi.map_view import normalize_events, read_map, turn_records
from argus.webapi.server import create_app


def sample(tmp_path):
    sid = "s-map-tests"
    write_session_meta(
        tmp_path, SessionMeta(id=sid, created=1, last_active=1, display_name="Conformal study")
    )
    life = tmp_path / "projects" / sid
    memory = LifeMemory.open(life)
    memory.backlog.add(
        BacklogItem(
            id="task-a", ts=1, title="Compare coverage", objective="Run 100 seeds", status="pending"
        )
    )
    (life / "events.jsonl").write_text(
        json.dumps({"type": "life.mission.started", "item_id": "task-a", "ts": 2})
        + "\n"
        + json.dumps({"type": "round.start", "round_index": 1, "ts": 3})
        + "\n"
    )
    return sid, life


def test_live_map_preserves_evidence_and_ignores_partial_line(tmp_path):
    sid, life = sample(tmp_path)
    with (life / "events.jsonl").open("a") as f:
        f.write('{"type":')
    data = read_map(sid, tmp_path, life)
    assert data["tasks"][0]["objective"] == "Run 100 seeds"
    assert data["events"][1]["association"] == "single_active_window"
    assert len(data["events"]) == 2
    assert read_map(sid, tmp_path, life)["events"] == data["events"]


def test_questions_answers_remarks_and_parks_are_on_the_map(tmp_path):
    sid, life = sample(tmp_path)
    memory = LifeMemory.open(life)
    # Answered in place: the task that asked resumed and is running again.
    memory.backlog.add(BacklogItem(
        id="ask", ts=4, title="Planner needs operator direction", objective="A or B", status="running",
        operator_decision={"status": "resolved", "manager_decision": "A"},
    ))
    memory.backlog.add(BacklogItem(id="budget", ts=10, title="Later", objective="Later", status="pending"))
    memory.backlog.update("budget", park_reason="budget_exhausted")
    with (life / "events.jsonl").open("a") as f:
        for row in (
            {"type": "life.observer.note", "agent_layer": "observer", "to": "manager", "ts": 4,
             "text": "The Engineer was dispatched before the operator chose."},
            {"type": "life.manager.supervision.applied", "agent_layer": "manager", "item_id": "task-a",
             "status": "applied", "action": "wait", "summary": "Wait for the operator's choice.", "ts": 5},
            {"type": "life.mission.completed", "item_id": "task-a", "status": "parked",
             "park_reason": "operator_question", "success": False, "ts": 6,
             "operator_question": "Which seed range?"},
            {"type": "life.operator_question.pending", "item_id": "ask", "agent_layer": "planner",
             "title": "Planner needs operator direction", "question": "A or B?", "options": ["A", "B"],
             "ts": 7},
            # Nothing runs: a remark naming no task has no task to stand on.
            {"type": "life.observer.note", "agent_layer": "observer", "to": "manager", "ts": 8, "text": "Idle."},
            {"type": "life.operator_question.answered", "item_id": "ask",
             "question": "A or B?", "manager_decision": "A", "option": "A", "decided_by": "operator", "ts": 9},
            {"type": "life.mission.started", "item_id": "ask", "agent_layer": "engineer", "ts": 10},
        ):
            f.write(json.dumps(row) + "\n")
    data = read_map(sid, tmp_path, life)
    said = {(e["item_id"], e["type"]): e for e in data["events"]}
    assert (said[("ask", "life.operator_question.pending")]["role"],
            said[("ask", "life.operator_question.pending")]["text"]) == ("planner", "A or B?\n1. A\n2. B")
    answered = said[("ask", "life.operator_question.answered")]
    assert (answered["role"], answered["text"], answered["option"]) == ("operator", "A", "A")
    note = said[("task-a", "life.observer.note")]
    assert (note["role"], note["association"]) == ("observer", "single_active_window")
    assert said[("task-a", "life.manager.supervision.applied")]["text"] == "Wait for the operator's choice."
    assert said[("task-a", "life.mission.completed")]["park_reason"] == "operator_question"
    # What a handoff asked the operator, so the map reads it as a question, not a block.
    assert said[("task-a", "life.mission.completed")]["operator_question"] == "Which seed range?"
    assert "operator_question" not in said[("ask", "life.mission.started")]
    assert "Idle." not in [e["text"] for e in data["events"]]
    tasks = {t["id"]: t for t in data["tasks"]}
    assert tasks["budget"]["park_reason"] == "budget_exhausted" and "park_reason" not in tasks["ask"]
    # The answered task reads as the work it is again, and depends on nothing new.
    assert (tasks["ask"]["status"], tasks["ask"]["deps"]) == ("running", [])
    # A task is in the hands of whoever wrote its latest record: the Engineer
    # took the answered task up again; one with no record names nobody.
    assert (tasks["ask"]["role"], tasks["task-a"]["role"]) == ("engineer", "engineer")
    assert "role" not in tasks["budget"]


def test_an_engineer_handoff_is_the_engineers_record_not_a_review():
    rows = [
        {"type": "life.mission.started", "item_id": "a", "ts": 1},
        {"type": "round.main.completed", "item_id": "a", "ts": 2, "round_index": 1,
         "last_message": "The two requirements conflict; the operator must choose.\n→ manager"},
        {"type": "round.review.completed", "item_id": "a", "ts": 3, "round_index": 1, "status": "blocked",
         "review_source": "engineer_handoff", "reason": "The two requirements conflict; the operator must choose."},
        {"type": "round.review.completed", "item_id": "a", "ts": 4, "round_index": 2, "status": "done",
         "review_source": "reviewer", "reason": "Passed."},
    ]
    roles = [(e["type"], e["role"]) for e in normalize_events(rows, {"a"})]
    assert roles[2:] == [("round.review.completed", "engineer"), ("round.review.completed", "reviewer")]


def test_a_task_the_reviewer_accepted_closes_under_the_reviewer():
    done = {"type": "life.mission.completed", "item_id": "a", "agent_layer": "engineer", "success": True}
    rows = [{**done, "ts": 1, "review_source": "reviewer"},
            {**done, "ts": 2, "review_source": "engineer_self_review"},
            {**done, "ts": 3, "review_source": "reviewer", "success": False}]
    assert [e["role"] for e in normalize_events(rows, {"a"})] == ["reviewer", "engineer", "engineer"]


def test_ambiguous_rounds_do_not_attach_to_arbitrary_tasks():
    rows = [{"type": "life.mission.started", "item_id": t} for t in ("a", "b")]
    rows += [{"type": "round.start", "round_index": 1}]
    assert len(normalize_events(rows, {"a", "b"})) == 2


def test_map_generation_does_not_become_a_research_step():
    rows = [
        {"type": "life.mission.started", "item_id": "a"},
        {"type": "agent.message", "run_label": "map-summary", "text": "Card summary"},
        {"type": "round.start", "round_index": 1},
    ]
    events = normalize_events(rows, {"a"})
    assert [e["type"] for e in events] == ["life.mission.started", "round.start"]


def test_map_preserves_whether_a_review_actually_ran():
    rows = [
        {"type": "round.review.completed", "item_id": "a", "status": "continue",
         "review_skipped": skipped, "ts": ts}
        for ts, skipped in enumerate((True, False), 1)
    ]
    events = normalize_events(rows, {"a"})
    assert [event["review_skipped"] for event in events] == [True, False]
    assert all(event["status"] == "continue" for event in events)


def test_map_preserves_execution_completion_scope_without_inventing_legacy_flags():
    base = {"type": "life.mission.completed", "item_id": "a", "success": True}
    events = normalize_events([
        {**base, "ts": 1, "overall_complete": False, "campaign_continues": True,
         "attempt": 1, "stage_certification": "intentionally_skipped"},
        {**base, "ts": 2, "overall_complete": True, "campaign_continues": False},
        {**base, "ts": 3},
        {**base, "ts": 4, "overall_complete": "false", "campaign_continues": 1},
    ], {"a"})
    assert events[0]["success"] is True
    assert events[0]["overall_complete"] is False
    assert events[0]["campaign_continues"] is True
    assert events[0]["attempt"] == 1
    assert events[0]["stage_certification"] == "intentionally_skipped"
    assert events[1]["overall_complete"] is True
    assert events[1]["campaign_continues"] is False
    for event in events[2:]:
        assert "overall_complete" not in event
        assert "campaign_continues" not in event


def test_map_reads_certification_from_mission_outcome():
    events = normalize_events([{
        "type": "life.mission.completed", "item_id": "a", "ts": 1,
        "success": True, "overall_complete": False,
        "outcome": {"stage_certification": "intentionally_skipped"},
    }], {"a"})

    assert events[0]["stage_certification"] == "intentionally_skipped"


def _copy_stream_frames(response):
    return [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines()
            if line.startswith("data: ")]


@pytest.mark.parametrize("preview_cache_present", [False, True])
@pytest.mark.parametrize("mode,version", [("source-first", 25), ("learning-path", 27)])
def test_copy_preview_get_reads_only_its_cache_and_reports_its_version(tmp_path, monkeypatch, preview_cache_present, mode, version):
    sid, _ = sample(tmp_path)
    reads = []
    main = {"cards": {"main": {"title": "Existing explanation"}}, "relations": [], "cache_revision": 7}
    preview = ({"cards": {"preview": {"title": "Source-first candidate"}}, "relations": [], "cache_revision": 2}
               if preview_cache_present else {"cards": {}, "relations": [], "cache_revision": 0})
    source = "live:" + sid + ":zh-CN"

    def read_cache(root, key):
        assert root == tmp_path
        reads.append(key)
        return {source: main, source + ":" + mode: preview}[key]

    monkeypatch.setattr(copy, "read_cache", read_cache)
    client = TestClient(create_app(global_root=tmp_path))
    path = f"/api/map-copy/project/{sid}"
    normal = client.get(path).json()
    candidate = client.get(path, params={"preview": "true" if mode == "source-first" else mode}).json()
    assert normal["cards"] == main["cards"] and normal["version"] == copy.PROMPT_VERSION
    assert candidate["cards"] == preview["cards"] and candidate["version"] == version
    assert candidate["cache_revision"] == preview["cache_revision"] and normal["cache_revision"] == 7
    assert reads == [source, source + ":" + mode]


def test_concurrent_mission_events_keep_explicit_identity_without_mutation():
    from argus.life.supervisor._cost import _CostTrackingSink

    class Sink:
        def __init__(self):
            self.events = []

        def handle_event(self, event):
            self.events.append(event)

    downstream = Sink()
    a = _CostTrackingSink(
        downstream,
        engineer_model="gpt-5.6-sol",
        reviewer_model="gpt-5.6-sol",
        item_id="a",
        mission_id="a:attempt:2",
    )
    b = _CostTrackingSink(
        downstream, engineer_model="gpt-5.6-sol", reviewer_model="gpt-5.6-sol", item_id="b"
    )
    original = {"type": "round.start", "round_index": 1}
    a.handle_event(original)
    b.handle_event(original)
    assert "item_id" not in original
    assert [e["item_id"] for e in downstream.events] == ["a", "b"]


def test_public_completion_message_is_available_for_card_summaries():
    rows = [
        {
            "type": "round.main.completed",
            "item_id": "a",
            "ts": 2,
            "last_message": "Completed 200 independent seeds; coverage 0.902.",
        }
    ]
    value = normalize_events(rows, {"a"})
    assert value[0]["text"].startswith("Completed 200")


def test_card_key_cannot_overwrite_another_tasks_copy(tmp_path):
    sid, life = sample(tmp_path)
    data = read_map(sid, tmp_path, life)
    data["tasks"].append({"id": "task-b", "title": "Another study"})
    with pytest.raises(ValueError, match="card does not belong"):
        copy.card_evidence(
            data, [{"key": "task-b", "task_id": "task-a", "kind": "task", "event_ids": []}]
        )


def test_generation_schema_requires_every_card_exactly_once():
    cards = copy.schema(["task-a", "review-b"], ["task-a"])["properties"]["cards"]
    assert cards["type"] == "object"
    assert cards["required"] == ["task-a", "review-b"]
    assert set(cards["properties"]) == {"task-a", "review-b"}
    assert cards["additionalProperties"] is False


def _progress(kind, item_id, ts, **extra):
    return {"type": "engineer.progress", "kind": kind, "item_id": item_id, "ts": ts,
            "agent_layer": "engineer", **extra}


def test_engineer_progress_folds_into_work_segments_with_narration_and_steps():
    rows = [
        {"type": "life.mission.started", "item_id": "a", "ts": 1},
        _progress("agent_message", "a", 2, text="先读设计规范，再生成幻灯片。"),
        _progress("reasoning", "a", 2.5, text="private"),
        _progress("tool_use", "a", 3, text='view: {"path": "spec.md"}', tool_name="view", status="running"),
        _progress("command_execution", "a", 4, text="python build.py", tool_name="Build the deck"),
        _progress("agent_message", "a", 5, text="八页已生成，开始校验。"),
        _progress("tool_use", "a", 6, text='view: {"path": "render.png"}', tool_name="view"),
        {"type": "round.main.completed", "item_id": "a", "ts": 7, "last_message": "RESULT=done"},
        _progress("agent_message", "a", 8, text="第二轮开始。"),
    ]
    segments: dict = {}
    events = normalize_events(rows, {"a"}, set(), segments)
    work = [e for e in events if e["type"] == "work.segment"]
    assert [e["id"] for e in work] == ["seg:a:1", "seg:a:2"]
    assert work[0]["text"] == "先读设计规范，再生成幻灯片。"
    assert [s["kind"] for s in work[0]["steps"]] == ["tool_use", "command_execution"]
    assert work[0]["steps"][1] == {
        "kind": "command_execution", "label": "运行 build.py", "ts": 4.0, "tool": "Build the deck",
    }
    assert work[0]["ts"] == 2.0 and work[0]["ts_end"] == 4.0
    assert work[1]["text"] == "八页已生成，开始校验。" and len(work[1]["steps"]) == 1
    # The round ended before this narration, so it starts a fresh segment that is still open.
    # Words with no work after them yet wait: they may be the agent's last message.
    assert segments["open"]["a"]["text"] == "第二轮开始。" and segments["open"]["a"]["steps"] == []
    # Reasoning never reaches the map; the original mission events are untouched.
    assert [e["type"] for e in events if e["type"] != "work.segment"] == [
        "life.mission.started", "round.main.completed",
    ]


def test_a_tasks_work_holds_its_own_roles_once_in_the_order_it_happened(tmp_path):
    rows = [
        {"type": "life.mission.started", "item_id": "a", "ts": 1},
        _progress("agent_message", "a", 2, text="Read the code."),
        _progress("tool_use", "a", 3, text="view: a", tool_name="view"),
        # The Planner runs beside the task and names none; the Manager's stage call names it.
        {**_progress("agent_message", "", 4, text="Planning the project."), "agent_layer": "planner"},
        {**_progress("tool_use", "", 5, text="view: plan", tool_name="view"), "agent_layer": "planner"},
        {**_progress("tool_use", "a", 6, text="view: stage", tool_name="view"), "agent_layer": "manager"},
        _progress("tool_use", "a", 7, text="view: b", tool_name="view"),
        # The Engineer's last message is the round's record.
        _progress("agent_message", "a", 8, text="Done: 3 passed."),
        {"type": "round.main.completed", "item_id": "a", "ts": 9, "last_message": "Done: 3 passed.\n→ manager"},
    ]
    events = normalize_events(rows, {"a"}, set(), {})
    work = [e for e in events if e["type"] == "work.segment"]
    assert [(e["role"], e["text"], len(e["steps"])) for e in work] == [("engineer", "Read the code.", 2)]

    # A segment closes after the records that followed it began: read in the order things happened.
    sid, life = sample(tmp_path)
    with (life / "events.jsonl").open("a") as f:
        for row in (
            _progress("agent_message", "task-a", 4, text="Checking."),
            _progress("tool_use", "task-a", 5, text="view: a", tool_name="view"),
            {"type": "round.main.completed", "item_id": "task-a", "ts": 6, "last_message": "Checked."},
            {"type": "life.mission.completed", "item_id": "task-a", "ts": 7, "status": "done"},
        ):
            f.write(json.dumps(row) + "\n")
    times = [e["ts"] for e in read_map(sid, tmp_path, life)["events"]]
    assert times == sorted(times) and len(times) == 5


def test_a_running_turn_shows_its_steps_as_they_are_journaled():
    rows = [
        {"type": "manager.turn.started", "message_id": "web-5", "ts": 1, "text": "Add slugify"},
        {"type": "engineer.progress", "kind": "tool_use", "agent_layer": "manager", "item_id": "turn:web-5",
         "message_id": "web-5:c1", "text": "查阅 core.py", "tool_name": "view", "status": "running",
         "turn_step": True, "ts": 2},
        {"type": "engineer.progress", "kind": "tool_use", "agent_layer": "manager", "item_id": "turn:web-5",
         "message_id": "web-5:c1", "text": "查阅 core.py", "tool_name": "view", "status": "completed",
         "turn_step": True, "ts": 3},
    ]
    (work,) = turn_records(rows, {}, {})["turn:web-5"]["events"]
    assert work["steps"] == [{"kind": "tool_use", "label": "查阅 core.py", "ts": 2.0, "tool": "view",
                              "status": "completed"}]
    assert "tool_details_recorded" not in work


def test_team_tool_steps_read_as_plain_actions_without_absolute_paths():
    rows = [
        {"type": "life.mission.started", "item_id": "a", "ts": 1},
        _progress("tool_use", "a", 2, text='view: {"path": "/proj/src/main.py"}', tool_name="view"),
        _progress("tool_use", "a", 3, text='view: {"path": "/runtime/state/skills/playbook.md"}', tool_name="view"),
        _progress("tool_use", "a", 4, text='glob: {"pattern": "**/*.py"}', tool_name="glob"),
    ]
    segments: dict = {}
    events = normalize_events(rows, {"a"}, set(), segments, "/proj")
    steps = [s for e in events if e["type"] == "work.segment" for s in e["steps"]]
    assert [s["label"] for s in steps] == ["查阅 main.py", "查找 .py 文件"], "reading a skill is no step"


def test_command_steps_read_like_turn_steps_without_backend_session_paths():
    files = "/runtime/state/copilot-home/session-state/0982/files"
    rows = [
        {"type": "life.mission.started", "item_id": "a", "ts": 1},
        _progress("command_execution", "a", 2, text=f"mkdir -p {files} && pytest -q | tee {files}/pytest.log"),
    ]
    events = normalize_events(rows, {"a"}, set(), {}, "/proj")
    (step,) = [s for e in events if e["type"] == "work.segment" for s in e["steps"]]
    assert step["label"] == "运行测试"


def test_steps_that_already_read_plainly_are_not_relabelled_again():
    # Manager turn steps are stored already labelled; older ones stored
    # "⚙ name · {json}" with the full arguments in ``detail``.
    rows = [
        {"type": "life.mission.started", "item_id": "a", "ts": 1},
        _progress("tool_use", "a", 2, text="查阅 Argus 内部文件", tool_name="view", turn_step=True),
        _progress("tool_use", "a", 3, text='⚙ view · {"path": "/proj/src/ma…', tool_name="view",
                  turn_step=True, detail='{"path": "/proj/src/main.py"}'),
    ]
    events = normalize_events(rows, {"a"}, set(), {}, "/proj")
    steps = [s for e in events if e["type"] == "work.segment" for s in e["steps"]]
    assert [s["label"] for s in steps] == ["查阅 Argus 内部文件", "查阅 main.py"]


def test_turn_cards_saved_with_raw_tool_labels_read_plainly():
    detail = '{"cells": null, "includeOutputs": null, "limit": 100, "path": "/proj/README.md"}'
    rows = [
        {"type": "ui.operator", "message_id": "web-9-operator", "ts": 9, "text": "看看 README"},
        {"type": "ui.argus", "message_id": "web-9-argus", "ts": 13, "text": "好了。", "steps": [
            {"kind": "tool_use", "label": '⚙ read · {"cells": null, "includeOutputs": null, "limit"…',
             "tool": "read", "detail": detail, "status": "completed", "started_ts": 10.0, "ended_ts": 11.0},
            {"kind": "command_execution", "label": "$ wc -l README.md", "tool": "bash",
             "status": "completed", "started_ts": 11.0, "ended_ts": 12.0},
        ]},
    ]
    turns = turn_records(rows, {}, {}, "/proj")
    work = turns["turn:web-9"]["events"][0]
    assert [s["label"] for s in work["steps"]] == ["查阅 README.md", "$ wc -l README.md"]


def test_two_narrations_without_work_between_them_are_one_thought():
    rows = [
        _progress("agent_message", "a", 1, text="先看一眼。"),
        _progress("agent_message", "a", 2, text="没有问题。"),
        _progress("tool_use", "a", 3, text="view: x", tool_name="view"),
    ]
    events = normalize_events(rows, {"a"}, set(), {})
    (segment,) = [e for e in events if e["type"] == "work.segment"]
    assert segment["text"] == "先看一眼。\n\n没有问题。"
    assert len(segment["steps"]) == 1


def test_an_open_segment_keeps_growing_across_incremental_reads(tmp_path):
    sid, life = sample(tmp_path)
    with (life / "events.jsonl").open("a") as f:
        f.write(json.dumps(_progress("agent_message", "task-a", 4, text="开始动手。")) + "\n")
        f.write(json.dumps(_progress("tool_use", "task-a", 5, text="view: a", tool_name="view")) + "\n")
    state: dict = {}
    first = read_map(sid, tmp_path, life, event_state=state)
    (segment,) = [e for e in first["events"] if e["type"] == "work.segment"]
    assert len(segment["steps"]) == 1
    with (life / "events.jsonl").open("a") as f:
        f.write(json.dumps(_progress("tool_use", "task-a", 6, text="view: b", tool_name="view")) + "\n")
    second = read_map(sid, tmp_path, life, event_state=state)
    grown = [e for e in second["events"] if e["type"] == "work.segment"]
    assert [e["id"] for e in grown] == ["seg:task-a:1"]
    assert len(grown[0]["steps"]) == 2
    assert grown[0]["revision"] != segment["revision"]


def test_a_chat_turn_with_tool_steps_becomes_a_card_on_the_map(tmp_path):
    sid, life = sample(tmp_path)
    steps = [
        {"kind": "command_execution", "label": "$ wc -l README.md", "tool": "Count README lines",
         "call_id": "c1", "status": "completed", "started_ts": 10.0, "ended_ts": 12.0, "output": "1"},
    ]
    with (life / "events.jsonl").open("a") as f:
        f.write(json.dumps({"type": "ui.operator", "message_id": "web-7-operator", "ts": 9,
                            "text": "README 有几行？"}) + "\n")
        f.write(json.dumps({"type": "ui.argus", "message_id": "web-7-argus", "ts": 13,
                            "text": "一行。", "steps": steps}) + "\n")
        f.write(json.dumps({"type": "ui.operator", "message_id": "web-8-operator", "ts": 14,
                            "text": "谢谢"}) + "\n")
        f.write(json.dumps({"type": "ui.argus", "message_id": "web-8-argus", "ts": 15,
                            "text": "不客气。"}) + "\n")
    state: dict = {}
    data = read_map(sid, tmp_path, life, event_state=state)
    cards = [t for t in data["tasks"] if t.get("kind") == "turn"]
    assert [c["id"] for c in cards] == ["turn:web-7"]
    card = cards[0]
    assert card["title"] == "README 有几行？" and card["status"] == "done"
    assert card["summary"] == "一行。" and card["role"] == "manager"
    assert card["started_ts"] == 10.0 and card["finished_ts"] == 13.0
    owned = [e for e in data["events"] if e["item_id"] == "turn:web-7"]
    assert [e["type"] for e in owned] == ["work.segment", "turn.replied"]
    assert owned[0]["steps"] == [{
        "kind": "command_execution", "label": "$ wc -l README.md", "ts": 10.0,
        "tool": "Count README lines", "status": "completed",
    }]
    assert owned[1]["text"] == "一行。"
    # A later read that only sees new rows keeps the card.
    with (life / "events.jsonl").open("a") as f:
        f.write(json.dumps({"type": "round.start", "round_index": 2, "ts": 20}) + "\n")
    again = read_map(sid, tmp_path, life, event_state=state)
    assert [t["id"] for t in again["tasks"] if t.get("kind") == "turn"] == ["turn:web-7"]
    assert [e["id"] for e in again["events"] if e["item_id"] == "turn:web-7"] == [
        "turn:web-7:work", "turn:web-7:reply",
    ]


def test_step_less_self_delivery_becomes_a_map_card_but_chat_and_queue_receipts_do_not():
    rows = [
        {"type": "ui.operator", "message_id": "web-self-operator", "ts": 20,
         "text": "Build the requested artifact."},
        {"type": "ui.argus", "message_id": "web-self-argus", "ts": 21,
         "text": "Artifact delivered.", "mission_result": True, "success": True, "steps": []},
        {"type": "ui.operator", "message_id": "web-chat-operator", "ts": 22,
         "text": "Thanks."},
        {"type": "ui.argus", "message_id": "web-chat-argus", "ts": 23,
         "text": "You're welcome.", "steps": []},
        {"type": "ui.operator", "message_id": "web-queue-operator", "ts": 24,
         "text": "Queue another task."},
        {"type": "ui.argus", "message_id": "web-queue-argus", "ts": 25,
         "text": "Queued.", "mission_result": True, "success": True,
         "item_id": "task-queued", "steps": []},
    ]

    turns = turn_records(rows)

    assert list(turns) == ["turn:web-self"]
    turn = turns["turn:web-self"]
    assert turn["card"]["status"] == "done"
    assert turn["card"]["title"] == "Build the requested artifact."
    assert turn["events"][0]["steps"] == []
    assert turn["events"][0]["tool_details_recorded"] is False
    assert turn["events"][0]["association"] == "explicit"


def _legacy_solo_rows(*, failed=False, observed=True, run_label="self-implement"):
    return [
        {"type": "ui.operator", "message_id": "web-legacy-operator",
         "ts": 10, "text": "Create the workbook."},
        {"type": "agent.io.start", "call_id": "solo-1", "run_label": run_label, "ts": 11},
        {"type": "agent.io.complete", "call_id": "solo-1", "run_label": run_label, "ts": 12,
         "exit_code": 0, "turn_completed": True, "turn_failed": failed,
         "tool_activity_observed": observed, "fatal_error": "Hard idle timeout" if failed else None},
        {"type": "ui.argus", "message_id": "web-legacy-argus", "ts": 13,
         "text": "I am building it." if failed else "Created the workbook."},
    ]


@pytest.mark.parametrize(
    "run_label", ["map-summary", "curator.distill", "self-learning-review"],
)
def test_background_or_context_only_calls_do_not_recover_solo_cards(run_label):
    assert turn_records(_legacy_solo_rows(run_label=run_label)) == {}


def test_legacy_recovery_requires_observed_tools_and_an_unambiguous_start():
    assert turn_records(_legacy_solo_rows(observed=False)) == {}
    rows = _legacy_solo_rows()
    assert turn_records([rows[0], *rows[2:]]) == {}
    other = {**rows[0], "message_id": "web-other-operator"}
    assert turn_records([rows[0], other, *rows[1:]]) == {}


def test_question_card_lifecycle_is_incremental_and_cancellable():
    asks, turns = {}, {}
    rows = [{"type": "ui.operator", "message_id": "web-q-operator", "ts": 10, "text": "Explain SFT."},
            {"type": "manager.turn.started", "message_id": "web-q", "ts": 11, "text": "Explain SFT.", "turn_kind": "qa"}]
    card = turn_records(rows, turns, asks)["turn:web-q"]
    assert card["card"]["status"] == "running" and card["card"]["turn_kind"] == "qa"
    assert card["events"] == []
    turn_records([{"type": "manager.turn.cancelled", "message_id": "web-q", "ts": 12}], turns, asks)
    assert card["card"]["status"] == "cancelled"
    assert list(turns) == ["turn:web-q"]


@pytest.mark.parametrize("success", [True, False])
def test_explicit_question_completion_needs_no_tool_or_provider_receipt(success):
    rows = [{"type": "ui.operator", "message_id": "web-q-operator", "ts": 10, "text": "Explain SFT."},
            {"type": "ui.argus", "message_id": "web-q-argus", "ts": 12,
             "text": "The answer." if success else "The provider failed.", "turn_kind": "qa", "success": success}]
    turn = turn_records(rows)["turn:web-q"]
    assert turn["card"]["status"] == ("done" if success else "failed")
    assert [e["type"] for e in turn["events"]] == ["turn.replied"]


def test_durable_tool_steps_take_priority_over_legacy_execution_receipts():
    rows = _legacy_solo_rows()
    rows[-1]["steps"] = [{"kind": "tool_use", "label": "read: actual.csv",
                         "started_ts": 11, "ended_ts": 12, "status": "completed"}]
    work = turn_records(rows)["turn:web-legacy"]["events"][0]
    assert work["steps"][0]["label"] == "查阅 actual.csv"
    assert "tool_details_recorded" not in work
    assert work["association"] == "explicit"


def test_history_pages_carry_work_segments_and_turns_and_regrow_open_segments(tmp_path):
    from argus.webapi.map_history import history_page

    sid, life = sample(tmp_path)
    with (life / "events.jsonl").open("a") as f:
        f.write(json.dumps(_progress("agent_message", "task-a", 4, text="开始动手。")) + "\n")
        f.write(json.dumps(_progress("tool_use", "task-a", 5, text="view: a", tool_name="view")) + "\n")
        f.write(json.dumps({"type": "ui.operator", "message_id": "web-1-operator", "ts": 6, "text": "几行？"}) + "\n")
        f.write(json.dumps({"type": "ui.argus", "message_id": "web-1-argus", "ts": 7, "text": "一行。",
                            "steps": [{"kind": "tool_use", "label": "view: README", "status": "completed",
                                       "started_ts": 6.5, "ended_ts": 6.8}]}) + "\n")
    value = read_map(sid, tmp_path, life, include_events=False)
    page = history_page(tmp_path, life, value, None)
    kinds = sorted((e["item_id"], e["type"]) for e in page["events"])
    assert ("task-a", "work.segment") in kinds
    assert ("turn:web-1", "work.segment") in kinds and ("turn:web-1", "turn.replied") in kinds
    segment = next(e for e in page["events"] if e["id"] == "seg:task-a:1")
    assert len(segment["steps"]) == 1
    with (life / "events.jsonl").open("a") as f:
        f.write(json.dumps(_progress("tool_use", "task-a", 8, text="view: b", tool_name="view")) + "\n")
    again = history_page(tmp_path, life, value, page["history_cursor"])
    assert again["incremental"] is True
    regrown = [e for e in again["events"] if e["id"] == "seg:task-a:1"]
    assert len(regrown) == 1 and len(regrown[0]["steps"]) == 2
    assert not [e for e in again["events"] if e["item_id"] == "turn:web-1"]


def test_failure_metadata_forgets_a_failure_once_its_cooldown_has_passed() -> None:
    """The trial page showed "explanation unavailable" for an hour over a cache
    whose cooldown had ended at 04:12 (2026-09-16)."""
    import time as _time

    from argus.webapi import map_narrative

    fresh = map_narrative._failure_metadata(
        {"generation_error": {"code": "invalid_response"}, "retry_at": _time.time() + 120}
    )
    assert fresh["generation_error"]["code"] == "invalid_response" and fresh["retry_after"] > 0
    stale = map_narrative._failure_metadata(
        {"generation_error": {"code": "invalid_response"}, "retry_at": _time.time() - 1}
    )
    assert stale == {"generation_error": None, "retry_after": 0}


def test_reader_brief_accepts_next_nested_inside_scope_without_inventing_text() -> None:
    """The map model nested ``next`` under ``scope``; the page went without an
    explanation for the card twice on the trial (2026-09-16 04:07, 05:03)."""
    import pytest

    from argus.webapi import map_narrative

    nested = {
        "why": "为什么要做这一步。", "concept": None,
        "scope": {"scope": "这次做了什么。", "next": "接下来做什么。"},
    }
    assert map_narrative._reader_brief(nested) == {
        "why": "为什么要做这一步。", "scope": "这次做了什么。", "next": "接下来做什么。", "concept": None,
    }
    flat = {"why": "w", "concept": None, "scope": "s", "next": "n"}
    assert map_narrative._reader_brief(flat) == flat
    with pytest.raises(ValueError, match="invalid reader brief"):
        map_narrative._reader_brief({"why": "w", "concept": None, "scope": {"scope": "s"}})
