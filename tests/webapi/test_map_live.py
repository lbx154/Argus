import asyncio
import json
import threading

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


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("preview", [False, True, "learning-path"])
def test_copy_routes_require_auth_and_check_task_event_ownership(tmp_path, monkeypatch, stream, preview):
    sid, life = sample(tmp_path)
    client = TestClient(create_app(global_root=tmp_path, auth_token="test"))
    path = f"/api/map-copy/project/{sid}" + ("?stream=true" if stream else "")
    if preview:
        path += ("&" if stream else "?") + "preview=" + str(preview).lower()
    assert client.get(path).status_code == 401
    headers = {"Authorization": "Bearer test"}
    assert client.get(f"/api/projects/{sid}/map", headers=headers).status_code == 200
    bad = {
        "cards": [{"key": "a", "task_id": "task-a", "kind": "review", "event_ids": ["foreign"]}],
        "locale": "zh-CN",
    }
    assert client.post(path, json=bad).status_code == 401
    assert client.post(path, json=bad, headers=headers).status_code == 422
    assert not (tmp_path / "map-presentation").exists()


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
    monkeypatch.setattr(copy, "enrich", lambda *args, **kwargs: pytest.fail("A GET must not generate"))
    client = TestClient(create_app(global_root=tmp_path))
    path = f"/api/map-copy/project/{sid}"
    normal = client.get(path).json()
    candidate = client.get(path, params={"preview": "true" if mode == "source-first" else mode}).json()
    assert normal["cards"] == main["cards"] and normal["version"] == copy.PROMPT_VERSION
    assert candidate["cards"] == preview["cards"] and candidate["version"] == version
    assert candidate["cache_revision"] == preview["cache_revision"] and normal["cache_revision"] == 7
    assert reads == [source, source + ":" + mode]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("preview", [False, True, "learning-path"])
def test_copy_preview_post_reuses_enrichment_and_stream_without_changing_the_body(tmp_path, monkeypatch, stream, preview):
    sid, life = sample(tmp_path)
    body = {"cards": [{"key": "task-a", "task_id": "task-a", "kind": "task"}], "locale": "zh-CN"}
    calls = []
    result = {"cards": {}, "relations": [], "version": copy.copy_version(preview=preview)}

    def enrich(root, value, cards, locale, **kwargs):
        calls.append((root, cards, locale, kwargs))
        return result

    monkeypatch.setattr(copy, "enrich", enrich)
    client = TestClient(create_app(global_root=tmp_path))
    response = client.post(f"/api/map-copy/project/{sid}",
                           params={"stream": str(stream).lower(), "preview": str(preview).lower()}, json=body)
    assert response.status_code == 200
    if stream:
        assert _copy_stream_frames(response) == [{"type": "heartbeat", "quiet_s": 0}, {"type": "done", "result": result}]
    else:
        assert response.json() == result
    assert len(calls) == 1
    expected_kwargs = {"project_root": life, **({"preview": preview} if preview else {})}
    if stream:
        assert callable(calls[0][-1].get("on_progress"))
        expected_kwargs["on_progress"] = calls[0][-1]["on_progress"]
    assert calls == [(tmp_path, [{**body["cards"][0], "event_ids": []}], "zh-CN", expected_kwargs)]


def test_copy_stream_returns_the_complete_json_result_with_one_enrichment(tmp_path, monkeypatch):
    sid, life = sample(tmp_path)
    body = {"cards": [{"key": "task-a", "task_id": "task-a", "kind": "task"}]}
    result = {
        "cards": {"task-a": {"title": "覆盖率比较", "source_snapshot": {"version": 1}, "progress_source": {"source_id": "old"}}},
        "relations": [{"source": "a", "target": "b", "label": "uses", "evidence": "old justification"}],
        "cached": False, "version": copy.PROMPT_VERSION,
        "cache_revision": 3, "model_revision": "offline-fixture",
    }
    calls = []
    phases = ["waiting_for_source", "planning", "writing", "reviewing"]

    def enrich(root, value, cards, locale, **kwargs):
        calls.append((root, value["id"], cards, locale, kwargs))
        if on_progress := kwargs.get("on_progress"):
            for phase in phases:
                on_progress(phase)
        return result

    monkeypatch.setattr(copy, "enrich", enrich)
    client = TestClient(create_app(global_root=tmp_path))
    path = f"/api/map-copy/project/{sid}"
    expected = {**result, "cards": {"task-a": {"title": "覆盖率比较"}},
                "relations": [{"source": "a", "target": "b", "label": "uses"}]}
    response = client.post(path + "?stream=true", json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert _copy_stream_frames(response) == [
        {"type": "heartbeat", "quiet_s": 0},
        *[{"type": "progress", "phase": phase} for phase in phases],
        {"type": "done", "result": expected},
    ]
    assert len(calls) == 1 and callable(calls[0][-1].get("on_progress"))
    assert calls[0][-1] == {"project_root": life, "on_progress": calls[0][-1]["on_progress"]}
    ordinary = client.post(path, json=body)
    assert ordinary.status_code == 200 and ordinary.json() == expected
    assert "source_snapshot" in result["cards"]["task-a"]
    assert "progress_source" in result["cards"]["task-a"] and "evidence" in result["relations"][0]
    assert ordinary.headers["content-type"].startswith("application/json")
    assert len(calls) == 2 and calls[0][:-1] == calls[1][:-1]
    assert calls[1][-1] == {"project_root": life}


def test_copy_stream_delivers_progress_while_generation_is_still_running(tmp_path, monkeypatch):
    from argus.webapi.routes.map_live import MapCopyIn

    sid, _ = sample(tmp_path)
    release = threading.Event()
    completed = threading.Event()
    result = {"cards": {}, "relations": [], "cached": False}

    def enrich(*args, **kwargs):
        kwargs["on_progress"]("planning")
        assert release.wait(5), "The progress frame did not reach the reader"
        completed.set()
        return result

    monkeypatch.setattr(copy, "enrich", enrich)
    app = create_app(global_root=tmp_path)
    endpoint = next(route.endpoint for route in app.routes
                    if getattr(route, "path", "") == "/api/map-copy/{source}/{name}"
                    and "POST" in route.methods)
    received = []

    async def read_before_completion():
        response = await endpoint(
            "project", sid,
            MapCopyIn(cards=[{"key": "task-a", "task_id": "task-a", "kind": "task"}]),
            stream=True,
        )
        async for chunk in response.body_iterator:
            frame = json.loads(chunk.removeprefix("data: "))
            received.append(frame)
            if frame["type"] == "progress":
                assert not completed.is_set()
                release.set()

    try:
        asyncio.run(asyncio.wait_for(read_before_completion(), 2))
    finally:
        release.set()

    assert completed.is_set()
    assert received == [
        {"type": "heartbeat", "quiet_s": 0},
        {"type": "progress", "phase": "planning"},
        {"type": "done", "result": result},
    ]


@pytest.mark.parametrize("failure, status, detail", [
    (ValueError("invalid output"), 422, "card content could not be prepared"),
    (TimeoutError("model timed out"), 503, "card text is temporarily unavailable"),
    (RuntimeError("provider unavailable"), 503, "card text is temporarily unavailable"),
])
def test_copy_stream_reports_terminal_errors_and_keeps_json_http_errors(
    tmp_path, monkeypatch, failure, status, detail,
):
    sid, _ = sample(tmp_path)
    body = {"cards": [{"key": "task-a", "task_id": "task-a", "kind": "task"}]}
    calls = []

    def fail(*args, **kwargs):
        calls.append(kwargs)
        if on_progress := kwargs.get("on_progress"):
            on_progress("waiting_for_source")
            on_progress("planning")
        raise failure

    monkeypatch.setattr(copy, "enrich", fail)
    client = TestClient(create_app(global_root=tmp_path))
    path = f"/api/map-copy/project/{sid}"
    response = client.post(path + "?stream=true", json=body)
    assert response.status_code == 200
    assert _copy_stream_frames(response) == [
        {"type": "heartbeat", "quiet_s": 0},
        {"type": "progress", "phase": "waiting_for_source"},
        {"type": "progress", "phase": "planning"},
        {"type": "error", "error": detail, "status": status},
    ]
    ordinary = client.post(path, json=body)
    assert ordinary.status_code == status and ordinary.json() == {"detail": detail}
    assert len(calls) == 2
    assert callable(calls[0].get("on_progress")) and "on_progress" not in calls[1]


@pytest.mark.parametrize("preview", [False, True, "learning-path"])
def test_copy_stream_checks_project_session_and_card_ownership_before_generation(tmp_path, monkeypatch, preview):
    sid, _ = sample(tmp_path)
    body = {"cards": [{"key": "task-a", "task_id": "task-a", "kind": "task"}]}

    def unexpected(*args, **kwargs):
        pytest.fail("An invalid request started enrichment")

    monkeypatch.setattr(copy, "enrich", unexpected)
    client = TestClient(create_app(global_root=tmp_path))
    path = f"/api/map-copy/project/{sid}?stream=true"
    if preview:
        path += "&preview=" + str(preview).lower()
    assert client.post(path + "&session_id=s-foreign", json=body).status_code == 422
    assert client.post("/api/map-copy/project/s-missing?stream=true", json=body).status_code == 404
    for card in (
        {"key": "foreign", "task_id": "task-a", "kind": "task"},
        {"key": "task-a", "task_id": "foreign", "kind": "task"},
        {"key": "task-a", "task_id": "task-a", "kind": "task", "event_ids": ["foreign"]},
    ):
        response = client.post(path, json={"cards": [card]})
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/json")


def test_copy_stream_heartbeats_before_generation_and_keeps_cache_after_disconnect(tmp_path, monkeypatch):
    from argus.webapi import server
    from argus.webapi.routes.map_live import MapCopyIn

    sid, _ = sample(tmp_path)
    release = threading.Event()
    completed = threading.Event()
    calls = []
    original_enrich = copy.enrich

    def generate(documents, *args, **kwargs):
        calls.append(1)
        assert release.wait(5), "Offline generation fixture was not released"
        return {"cards": [{"key": document["key"], "title": "覆盖率比较",
                           "summary": "用独立样本检查覆盖率。", "detail": "比较覆盖率和区间宽度。"}
                          for document in documents], "relations": []}

    def enrich(*args, **kwargs):
        result = original_enrich(*args, **kwargs)
        completed.set()
        return result

    monkeypatch.setattr(copy, "generate", generate)
    monkeypatch.setattr(copy, "enrich", enrich)
    monkeypatch.setattr(copy, "configured", lambda: True)
    monkeypatch.setattr(server, "_manager_stream_heartbeat_seconds", lambda: 0.01)
    app = create_app(global_root=tmp_path)
    endpoint = next(route.endpoint for route in app.routes
                    if getattr(route, "path", "") == "/api/map-copy/{source}/{name}"
                    and "POST" in route.methods)
    received = []

    async def disconnect_while_generating():
        response = await endpoint(
            "project", sid,
            MapCopyIn(cards=[{"key": "task-a", "task_id": "task-a", "kind": "task"}]),
            stream=True,
        )
        disconnected = asyncio.Event()

        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                received.append(json.loads(message["body"].decode().removeprefix("data: ")))
                assert not completed.is_set()
                if sum(frame["type"] == "heartbeat" for frame in received) == 2:
                    disconnected.set()

        await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send), 2)

    try:
        asyncio.run(disconnect_while_generating())
        assert [frame["type"] for frame in received if frame["type"] != "progress"] == ["heartbeat", "heartbeat"]
        assert not completed.is_set()
    finally:
        release.set()
        assert completed.wait(2), "Disconnected generation did not finish caching"

    assert calls == [1]
    cache = copy.read_cache(tmp_path, "live:" + sid + ":zh-CN")
    assert cache["cards"]["task-a"]["title"] == "覆盖率比较"
    assert cache["cache_revision"] == 1


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
    result = copy.enrich(
        tmp_path,
        read_map(sid, tmp_path, life),
        [{"key": "task-a", "task_id": "task-a", "kind": "task", "event_ids": []}],
        "zh-CN", project_root=life,
    )
    assert result["generation_error"]["code"] == "invalid_response"
    assert result["retry_after"] > 0
    assert copy.read_cache(tmp_path, "live:" + sid + ":zh-CN").get("cards", {}) == {}


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


@pytest.mark.parametrize("failed", [False, True])
def test_legacy_solo_receipts_recover_truthful_execution_without_inventing_tools(failed):
    asks, turns = {}, {}
    rows = _legacy_solo_rows(failed=failed)
    assert turn_records(rows[:2], turns, asks) == {}
    assert turn_records(rows[2:3], turns, asks) == {}
    recovered = turn_records(rows[3:], turns, asks)["turn:web-legacy"]
    assert asks == {}
    assert recovered["card"]["status"] == ("failed" if failed else "done")
    assert recovered["card"]["started_ts"] == 11
    assert recovered["card"]["finished_ts"] == 13
    work, reply = recovered["events"]
    assert work["steps"] == []
    assert work["tool_details_recorded"] is False
    assert work["association"] == "single_active_window"
    assert reply["status"] == recovered["card"]["status"]
    if failed:
        assert work["text"] == recovered["card"]["summary"] == "Hard idle timeout"


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


@pytest.mark.parametrize("run_label", ["simple-1", "chat-1", "manager-quick-reply"])
def test_legacy_questions_recover_the_recorded_answer_without_execution_steps(run_label):
    rows = _legacy_solo_rows(run_label=run_label, observed=False)
    rows[-1]["text"] = "A detailed answer. " * 500
    turns, asks = {}, {}
    assert turn_records(rows[:2], turns, asks) == {}
    turn = turn_records(rows[2:], turns, asks)["turn:web-legacy"]
    assert turn["card"]["turn_kind"] == "qa"
    assert turn["card"]["status"] == "done"
    assert [e["type"] for e in turn["events"]] == ["turn.replied"]
    assert turn["events"][0]["text"] == rows[-1]["text"].strip()


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
    assert work["steps"][0]["label"] == "read: actual.csv"
    assert "tool_details_recorded" not in work
    assert work["association"] == "explicit"


def test_successful_solo_retry_does_not_inherit_an_earlier_failure():
    rows = _legacy_solo_rows(failed=True)
    retry = _legacy_solo_rows()
    retried = [
        *rows[:3],
        {**retry[1], "call_id": "solo-2", "ts": 13},
        {**retry[2], "call_id": "solo-2", "ts": 14},
        {**retry[3], "ts": 15},
    ]
    assert turn_records(retried)["turn:web-legacy"]["card"]["status"] == "done"


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


def test_question_cards_never_pay_for_generated_map_copy(tmp_path, monkeypatch):
    rows = _legacy_solo_rows(observed=False, run_label='simple-1')
    turn = turn_records(rows)['turn:web-legacy']
    dataset = {'id': 'qa-fixture', 'tasks': [turn['card']], 'events': turn['events']}
    monkeypatch.setattr(copy, 'run_map_model', lambda *a, **kw: pytest.fail('Q&A must use its saved answer'))
    result = copy.enrich(tmp_path, dataset, [{'key': turn['card']['id'], 'task_id': turn['card']['id'],
                                            'event_ids': []}], 'zh-CN', project_root=tmp_path)
    assert result['cards'] == {}


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
