import json

import pytest
from fastapi.testclient import TestClient

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.webapi import map_narrative as copy
from argus_skill.webapi.map_view import normalize_events, read_map
from argus_skill.webapi.server import create_app


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


def test_copy_routes_require_auth_and_check_task_event_ownership(tmp_path, monkeypatch):
    sid, life = sample(tmp_path)
    client = TestClient(create_app(global_root=tmp_path, auth_token="test"))
    path = f"/api/map-copy/project/{sid}"
    assert client.get(path).status_code == 401
    headers = {"Authorization": "Bearer test"}
    assert client.get(f"/api/projects/{sid}/map", headers=headers).status_code == 200
    bad = {
        "cards": [{"key": "a", "task_id": "task-a", "kind": "review", "event_ids": ["foreign"]}],
        "locale": "zh-CN",
    }
    assert client.post(path, json=bad, headers=headers).status_code == 422
    assert not (tmp_path / "map-presentation").exists()


def test_generation_is_cached_and_does_not_change_backlog(tmp_path, monkeypatch):
    sid, life = sample(tmp_path)
    data = read_map(sid, tmp_path, life)
    calls = []

    def generate(docs, tasks, locale, **kwargs):
        calls.append(docs)
        return {
            "cards": [
                {
                    "key": d["key"],
                    "title": "覆盖率比较",
                    "summary": "对照不同方法的区间覆盖率。",
                    "detail": "用独立校准集检验覆盖率与区间宽度。",
                }
                for d in docs
            ],
            "relations": [
                {"source": "ghost", "target": "task-a", "label": "坏引用", "evidence": "wrong"}
            ],
        }

    monkeypatch.setattr(copy, "generate", generate)
    monkeypatch.setattr(copy, "configured", lambda: True)
    before = (life / "backlog.jsonl").read_bytes()
    cards = [{"key": "task-a", "task_id": "task-a", "kind": "task", "event_ids": []}]
    one = copy.enrich(tmp_path, data, cards, "zh-CN", project_root=life)
    two = copy.enrich(tmp_path, data, cards, "zh-CN", project_root=life)
    assert two["cached"] and len(calls) == 1
    assert one["relations"] == []
    assert one["cards"]["task-a"]["summary"].startswith("对照")
    assert (life / "backlog.jsonl").read_bytes() == before


def test_invalid_generation_does_not_publish_partial_content(tmp_path, monkeypatch):
    sid, life = sample(tmp_path)
    monkeypatch.setattr(copy, "configured", lambda: True)
    monkeypatch.setattr(copy, "generate", lambda *_, **kwargs: {"cards": [], "relations": []})
    with pytest.raises(ValueError, match="coverage"):
        copy.enrich(
            tmp_path,
            read_map(sid, tmp_path, life),
            [{"key": "task-a", "task_id": "task-a", "kind": "task", "event_ids": []}],
            "zh-CN", project_root=life,
        )
    assert copy.read_cache(tmp_path, "live:" + sid + ":zh-CN").get("cards", {}) == {}


def test_concurrent_mission_events_keep_explicit_identity_without_mutation():
    from argus_skill.life.supervisor._cost import _CostTrackingSink

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


def test_copy_tracks_source_state_and_event_ids(tmp_path, monkeypatch):
    sid, life = sample(tmp_path)
    data = read_map(sid, tmp_path, life)
    monkeypatch.setattr(copy, "configured", lambda: True)
    monkeypatch.setattr(
        copy,
        "generate",
        lambda docs, *_, **kwargs: {
            "cards": [
                {
                    "key": d["key"],
                    "title": "方法比较",
                    "summary": "正在比较预测区间。",
                    "detail": "对比覆盖率与宽度。",
                }
                for d in docs
            ],
            "relations": [],
        },
    )
    event_id = data["events"][0]["id"]
    result = copy.enrich(
        tmp_path,
        data,
        [{"key": "task-a", "task_id": "task-a", "kind": "task", "event_ids": [event_id]}],
        "zh-CN", project_root=life,
    )
    assert result["cards"]["task-a"]["task_status"] == "pending"
    assert result["cards"]["task-a"]["event_ids"] == [event_id]
    assert result["cards"]["task-a"]["task_revision"] == data["tasks"][0]["revision"]
    assert result["cards"]["task-a"]["version"] == copy.PROMPT_VERSION


def test_generating_child_copy_keeps_outer_relations_until_tasks_change(tmp_path, monkeypatch):
    sid, life = sample(tmp_path)
    data = read_map(sid, tmp_path, life)
    data["tasks"].extend({"id": key, "title": key, "status": "pending"} for key in ("b", "c"))
    monkeypatch.setattr(copy, "configured", lambda: True)
    proposed_targets = iter(["b", "c", "d"])

    def generate(docs, *_, **kwargs):
        return {
            "cards": [
                {
                    "key": d["key"],
                    "title": "方法比较",
                    "summary": "比较覆盖率。",
                    "detail": "检验分布偏移。",
                }
                for d in docs
            ],
            "relations": [
                {
                    "source": "task-a",
                    "target": next(proposed_targets),
                    "label": "比较方法",
                    "evidence": "共同方法",
                }
            ],
        }

    monkeypatch.setattr(copy, "generate", generate)

    def enrich(key):
        return copy.enrich(
            tmp_path,
            data,
            [{"key": key, "task_id": "task-a", "kind": "plan", "event_ids": []}],
            "zh-CN", project_root=life,
        )

    outer = enrich("task-a")
    child = enrich("task-a:brief")
    assert child["relations"] == outer["relations"]
    assert child["relations"][0]["target"] == "b"
    data["tasks"].append({"id": "d", "title": "新实验", "status": "pending"})
    continued = enrich("task-a:outcome")["relations"]
    assert continued[0] == outer["relations"][0]
    assert [r["target"] for r in continued] == ["b", "d"]


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
    assert [e["id"] for e in work] == ["seg:a:1", "seg:a:2", "seg:a:3"]
    assert work[0]["text"] == "先读设计规范，再生成幻灯片。"
    assert [s["kind"] for s in work[0]["steps"]] == ["tool_use", "command_execution"]
    assert work[0]["steps"][1] == {
        "kind": "command_execution", "label": "python build.py", "ts": 4.0, "tool": "Build the deck",
    }
    assert work[0]["ts"] == 2.0 and work[0]["ts_end"] == 4.0
    assert work[1]["text"] == "八页已生成，开始校验。" and len(work[1]["steps"]) == 1
    # The round ended before this narration, so it starts a fresh segment that is still open.
    assert work[2]["text"] == "第二轮开始。" and work[2]["steps"] == []
    assert list(segments["open"]) == ["a"]
    # Reasoning never reaches the map; the original mission events are untouched.
    assert [e["type"] for e in events if e["type"] != "work.segment"] == [
        "life.mission.started", "round.main.completed",
    ]


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


def test_history_pages_carry_work_segments_and_turns_and_regrow_open_segments(tmp_path):
    from argus_skill.webapi.map_history import history_page

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
