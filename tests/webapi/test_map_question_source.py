"""Questions keep only the selected cached explanation and question-time records."""

import asyncio
import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import Backlog, BacklogItem
from argus.webapi import map_narrative, reader_foundation, reader_progress
from argus.webapi.map_feed import MapFeed
from argus.webapi.server import create_app


@pytest.fixture
def project(tmp_path, monkeypatch):
    sid = "s-lazy-source"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_session_meta(tmp_path, SessionMeta(id=sid, workdir=str(workspace)))
    life = tmp_path / "projects" / sid
    Backlog(life / "backlog.jsonl").add(BacklogItem(id="t", ts=1, title="A task", objective="Compare cases"))
    dataset = {
        "id": f"live:{sid}", "tasks": [
            {"id": "t", "title": "Task when asked", "objective": "CURRENT OBJECTIVE " + "x" * 1500, "status": "done"},
            {"id": "other", "title": "Another task", "objective": "DO NOT INCLUDE OTHER TASK", "status": "done"},
        ],
        "events": [
            {"id": f"event-{i}", "item_id": "t", "type": "round.main.completed", "ts": i + 1,
             "text": f"CURRENT EVENT {i} " + "x" * 2000, "role": "engineer"} for i in range(6)
        ],
    }
    card = {
        "title": "The selected explanation", "summary": "SELECTED EXPLANATION", "detail": "Original interpretation.",
        "version": 26, "copy_revision": 3, "generated_at": 100.5, "input_revision": "original-input",
        "event_ids": [*(event["id"] for event in dataset["events"]), "lost-event"],
    }
    monkeypatch.setattr(MapFeed, "read", lambda *a, **k: copy.deepcopy(dataset))
    monkeypatch.setattr(map_narrative, "_source_lock", lambda *a, **k: pytest.fail("Question acquired generation lock"))
    monkeypatch.setattr(map_narrative, "enrich", lambda *a, **k: pytest.fail("Question generated copy"))
    monkeypatch.setattr(map_narrative, "run_map_model", lambda *a, **k: pytest.fail("Question called copy model"))
    return sid, life, workspace, dataset, card


def save_copy(root, sid, card, *, key="t", locale="en-US", preview=False, foundation_id=None):
    source = map_narrative.copy_source(f"live:{sid}", locale, preview=preview, foundation_id=foundation_id)
    path = map_narrative.cache_path(root, source)
    path.parent.mkdir(exist_ok=True)
    map_narrative._write_cache(path, {"cards": {key: card}, "cache_revision": 3})
    return path


@pytest.mark.parametrize("preview", [False, True, "learning-path", "question-foundation"])
@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_question_route_reads_the_selected_mode_without_generating_and_deduplicates(project, tmp_path, monkeypatch, preview, locale):
    sid, life, workspace, dataset, card = project
    foundation_id = str(uuid4()) if preview == "question-foundation" else None
    path = save_copy(tmp_path, sid, card, locale=locale, preview=preview, foundation_id=foundation_id)
    before = path.read_bytes()
    original = copy.deepcopy(card)
    read_cache = map_narrative.read_cache

    def worker_read(*args):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return read_cache(*args)

    monkeypatch.setattr(map_narrative, "read_cache", worker_read)
    assert not (life / reader_progress.DIRECTORY).exists()
    client = TestClient(create_app(global_root=tmp_path))
    body = {"card_key": "t", "task_id": "t", "locale": locale, "preview": preview, "foundation_id": foundation_id}
    url = f"/api/map-question-source/project/{sid}"
    first = client.post(url, params={"session_id": sid}, json=body)
    assert first.status_code == 200, first.text
    ref = first.json()
    assert set(ref) == {"source_id", "title", "generated_at", "path", "task_id", "card_key", "copy_revision"}
    record = reader_progress.read_progress_source(tmp_path, sid, ref["source_id"])
    assert record["locale"] == locale and record["card"] == original
    assert record["copy_source"] == map_narrative.copy_source(dataset["id"], locale, preview=preview, foundation_id=foundation_id)
    assert set(record["records"]) == {"task_id", "task", "events"}
    assert record["records"]["task"]["objective_truncated"] is True
    assert [event["id"] for event in record["records"]["events"]] == [f"event-{i}" for i in range(2, 6)]
    assert all(event["text_truncated"] for event in record["records"]["events"])
    assert "DO NOT INCLUDE OTHER TASK" not in record["document"] and "SELECTED EXPLANATION" in record["document"]
    assert "source_snapshot" not in record and "resolved_evidence" not in record
    artifact = workspace / ref["path"]
    artifact_before = artifact.read_bytes()
    dataset["tasks"][0]["objective"] = "LATER EDIT"
    dataset["events"][0]["text"] = "LATER EVENT"
    assert client.post(url, json=body).json() == ref
    assert artifact.read_bytes() == artifact_before and path.read_bytes() == before
    metadata = client.get(f"/api/projects/{sid}/artifact", params={"path": ref["path"]})
    assert metadata.status_code == 200 and metadata.json()["progress_source"] == ref
    raw = client.get(f"/api/projects/{sid}/artifact/raw", params={"path": ref["path"]})
    assert raw.status_code == 200 and raw.text == record["document"]
    assert client.get(f"/api/projects/{sid}/artifacts").json() == {"artifacts": []}
    assert not (life / reader_foundation.MANIFEST_DIRECTORY).exists()


@pytest.mark.parametrize("binding", ["task_id", "source_snapshot"])
def test_missing_historical_events_are_omitted_when_saved_card_has_task_binding(project, tmp_path, binding):
    sid, _, _, _, card = project
    if binding == "task_id":
        card["task_id"] = "t"
    else:
        card["source_snapshot"] = {"task_id": "t", "card_key": "lost-event", "events": [{"text": "OLD EXCERPT"}]}
    save_copy(tmp_path, sid, card, key="lost-event")
    response = TestClient(create_app(global_root=tmp_path)).post(
        f"/api/map-question-source/project/{sid}", json={"card_key": "lost-event", "task_id": "t", "locale": "en-US"},
    )
    assert response.status_code == 200, response.text
    record = reader_progress.read_progress_source(tmp_path, sid, response.json()["source_id"])
    assert "OLD EXCERPT" not in json.dumps(record)
    assert record["card_key"] == "lost-event"


def test_saved_task_binding_preserves_the_selected_brief_card_evidence(project, tmp_path):
    sid, _, _, _, card = project
    card["task_id"] = "t"
    save_copy(tmp_path, sid, card, key="t:brief")
    response = TestClient(create_app(global_root=tmp_path)).post(
        f"/api/map-question-source/project/{sid}", json={"card_key": "t:brief", "task_id": "t", "locale": "en-US"},
    )
    assert response.status_code == 200, response.text
    record = reader_progress.read_progress_source(tmp_path, sid, response.json()["source_id"])
    assert "status" not in record["records"]["task"] and "outcome" not in record["records"]["task"]


@pytest.mark.parametrize("problem", [
    "missing_copy", "missing_card", "wrong_task", "wrong_event_owner", "wrong_bound_task",
    "missing_task", "no_title", "no_revision", "no_timestamp", "unknown_foundation", "missing_foundation",
])
def test_unavailable_explanation_or_wrong_task_returns_source_error_without_writing(project, tmp_path, problem):
    sid, life, _, dataset, card = project
    body = {"card_key": "t", "task_id": "t", "locale": "en-US"}
    if problem == "missing_card":
        body["card_key"] = "absent"
    elif problem == "wrong_task":
        body["task_id"] = "other"
    elif problem == "wrong_event_owner":
        dataset["events"][0]["item_id"] = "other"
    elif problem == "wrong_bound_task":
        card["task_id"] = "other"
    elif problem == "missing_task":
        dataset["tasks"] = dataset["tasks"][1:]
    elif problem.startswith("no_"):
        card.pop({"no_title": "title", "no_revision": "copy_revision", "no_timestamp": "generated_at"}[problem])
    elif problem in {"unknown_foundation", "missing_foundation"}:
        body["preview"] = "question-foundation"
        if problem == "unknown_foundation":
            body["foundation_id"] = str(uuid4())
    if problem != "missing_copy":
        save_copy(tmp_path, sid, card)
    response = TestClient(create_app(global_root=tmp_path)).post(f"/api/map-question-source/project/{sid}", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "reader_source_unavailable"
    assert not (life / reader_progress.DIRECTORY).exists()


def test_route_auth_session_source_and_body_limits(project, tmp_path):
    sid, life, _, _, card = project
    save_copy(tmp_path, sid, card, locale="zh-CN")
    client = TestClient(create_app(global_root=tmp_path, auth_token="offline-test-token"))
    body = {"card_key": "t", "task_id": "t"}
    url = f"/api/map-question-source/project/{sid}"
    assert client.post(url, json=body).status_code == 401
    client.headers["Authorization"] = "Bearer offline-test-token"
    assert client.post(url, params={"session_id": "s-other"}, json=body).status_code == 422
    assert client.post(url.replace("/project/", "/dataset/"), json=body).status_code == 422
    for field, value in [("card_key", "x" * 301), ("task_id", "x" * 201), ("locale", "fr"), ("preview", "invalid")]:
        assert client.post(url, json={**body, field: value}).status_code == 422
    assert not (life / reader_progress.DIRECTORY).exists()
    assert client.post(url, json=body).status_code == 200


def test_prepare_then_question_uses_the_existing_foundation_contract(project, tmp_path, monkeypatch):
    sid, _, _, _, card = project
    save_copy(tmp_path, sid, card)
    prompts = []
    monkeypatch.setattr(reader_foundation, "resolve_map_model", lambda: SimpleNamespace(available=True, revision="offline"))

    def answer(prompt, *args, **kwargs):
        prompts.append(prompt)
        return {"title": "Answer", "markdown": "# Answer\n\nThe selected explanation covers one recorded comparison."}

    monkeypatch.setattr(reader_foundation, "run_map_model", answer)
    client = TestClient(create_app(global_root=tmp_path))
    prepared = client.post(f"/api/map-question-source/project/{sid}", json={"card_key": "t", "task_id": "t", "locale": "en-US"})
    assert prepared.status_code == 200, prepared.text
    response = client.post(f"/api/projects/{sid}/reader-foundation", json={
        "request_id": str(uuid4()), "question": "Explain this comparison.", "locale": "en-US",
        "progress_source": {"source_id": prepared.json()["source_id"]},
    })
    assert response.status_code == 200, response.text
    assert response.json()["reader_foundation"]["kind"] == "progress_answer"
    assert len(prompts) == 1 and "SELECTED EXPLANATION" in prompts[0] and "CURRENT EVENT 5" in prompts[0]
    assert "source_snapshot" not in prompts[0] and "resolved_evidence" not in prompts[0]
