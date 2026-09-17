"""History and cached evidence must apply the live feed's numeric boundary."""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from argus.core.json_codec import loads_finite_json
from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi import map_history, map_narrative
from argus.webapi.map_view import read_map
from argus.webapi.server import create_app


def _project(root):
    sid = "s-finite-history"
    life = root / "projects" / sid
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem(id="A", ts=1, title="Task", objective="Work", status="running"))
    write_session_meta(root, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    path = life / "events.jsonl"
    path.write_text(json.dumps({
        "type": "life.mission.started", "event_id": "start", "item_id": "A", "ts": 1,
    }) + "\n", encoding="utf-8")
    return sid, life, path


def _review(event_id, timestamp):
    return {"type": "round.review.completed", "event_id": event_id, "item_id": "A",
            "status": "continue", "reason": "Add evidence", "round_index": 1, "ts": timestamp}


def _append_bad_then_good(path, token):
    bad = json.dumps(_review("bad", 0)).replace('"ts": 0', '"ts": ' + token)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(bad + "\n" + json.dumps(_review("good", 3)) + "\n")


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_history_http_skips_bad_ids_and_keeps_pagination_progress(tmp_path, monkeypatch, token):
    sid, life, path = _project(tmp_path)
    _append_bad_then_good(path, token)
    original = path.read_bytes()
    monkeypatch.setattr(map_history, "PAGE_EVENTS", 1)
    client = TestClient(create_app(global_root=tmp_path))
    seen, cursor = [], None
    for _ in range(8):
        response = client.get(
            f"/api/projects/{sid}/map-history", params={"after": cursor} if cursor else {},
        )
        assert response.status_code == 200, response.text
        page = loads_finite_json(response.content)
        seen.extend(event["id"] for event in page["events"])
        cursor = page["history_cursor"]
        if not page["history_loading"]:
            break
    else:
        pytest.fail("history did not finish paging past the invalid event")
    assert seen == ["start", "good"]
    assert page["history_progress"]["loaded_bytes"] == len(original)
    assert path.read_bytes() == original
    next_page = client.get(f"/api/projects/{sid}/map-history", params={"after": cursor}).json()
    assert next_page["events"] == []
    assert next_page["history_cursor"] == cursor
    assert {row["id"] for row in map_history.indexed_evidence(tmp_path, life, ["bad", "good"])} == {"good"}


@pytest.mark.parametrize("bad_metadata", [False, True])
def test_old_history_index_rebuilds_and_resets_cursor(tmp_path, bad_metadata):
    sid, life, path = _project(tmp_path)
    _append_bad_then_good(path, "NaN")
    original = path.read_bytes()
    value = read_map(sid, tmp_path, life, include_events=False)
    prior = map_history.history_page(tmp_path, life, value, None)
    cache = map_history.history_path(tmp_path, life)
    with sqlite3.connect(cache) as db:
        state = json.loads(db.execute("SELECT value FROM metadata WHERE key='state'").fetchone()[0])
        state["version"] = 3
        if bad_metadata:
            state["segments"] = {"poisoned": float("nan")}
        db.execute("UPDATE metadata SET value=? WHERE key='state'", (json.dumps(state),))
        db.execute("INSERT OR REPLACE INTO events VALUES (?, ?, ?)", (
            999, "cached-bad", json.dumps({"id": "cached-bad", "item_id": "A", "ts": float("nan")}),
        ))

    response = TestClient(create_app(global_root=tmp_path)).get(
        f"/api/projects/{sid}/map-history", params={"after": prior["history_cursor"]},
    )
    assert response.status_code == 200, response.text
    page = loads_finite_json(response.content)
    assert page["reset_history"] is True
    assert page["incremental"] is False
    assert page["history_cursor"] != prior["history_cursor"]
    assert [event["id"] for event in page["events"]] == ["start", "good"]
    assert path.read_bytes() == original
    with sqlite3.connect(cache) as db:
        state = loads_finite_json(db.execute("SELECT value FROM metadata WHERE key='state'").fetchone()[0])
        assert state["version"] == map_history.HISTORY_VERSION
        assert {row[0] for row in db.execute("SELECT id FROM events")} == {"start", "good"}


def test_old_cached_evidence_filters_bad_ids_before_http_summary_context(tmp_path, monkeypatch):
    sid, life, _path = _project(tmp_path)
    map_history.history_page(tmp_path, life, read_map(sid, tmp_path, life, include_events=False), None)
    cache = map_history.history_path(tmp_path, life)
    with sqlite3.connect(cache) as db:
        state = json.loads(db.execute("SELECT value FROM metadata WHERE key='state'").fetchone()[0])
        state["version"] = 3
        db.execute("UPDATE metadata SET value=? WHERE key='state'", (json.dumps(state),))
        for seq, event_id, timestamp in ((20, "cached-bad", float("nan")), (21, "cached-good", 2)):
            event = {"id": event_id, "item_id": "A", "type": "round.review.completed",
                     "ts": timestamp, "text": "Cached evidence", "role": "reviewer"}
            db.execute("INSERT INTO events VALUES (?, ?, ?)", (seq, event_id, json.dumps(event)))
    before = cache.read_bytes()
    ids = ["cached-bad", "cached-good"]
    assert [row["id"] for row in map_history.indexed_evidence(tmp_path, life, ids)] == ["cached-good"]

    def inspect_context(root, value, cards, locale, *, project_root):
        # Exercise real HTTP evidence loading, replacing only model generation.
        assert {event["id"] for event in value["events"]} == {"start", "cached-good"}
        json.dumps(value, allow_nan=False)
        return {"evidence_ids": [event["id"] for event in value["events"]]}

    monkeypatch.setattr(map_narrative, "enrich", inspect_context)
    response = TestClient(create_app(global_root=tmp_path)).post(
        f"/api/map-copy/project/{sid}",
        json={"cards": [{"key": "cached-good", "task_id": "A", "kind": "review", "event_ids": ids}]},
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["evidence_ids"]) == {"start", "cached-good"}
    assert cache.read_bytes() == before  # Evidence reads do not migrate/write the cache.
