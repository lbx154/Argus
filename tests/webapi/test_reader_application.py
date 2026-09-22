"""Offline application/ownership checks; fixture prose is not a teaching grade."""

import copy
import json
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi import map_lesson, map_narrative, reader_application, reader_foundation
from argus.webapi.map_model import MapModel
from argus.webapi.map_teaching_review import TEACHING_CORE
from argus.webapi.server import create_app


def foundation(**changes):
    return {"id": "saved-question", "path": "reader-notes/saved-question.md", "question": "How is coverage compared?",
            "version": 1, "state": "complete", "markdown": "# Saved foundation\nThe recorded comparison rule.", **changes}


def card():
    return {"title": "Compare the recorded case", "summary": "The source reports a bounded result.",
            "detail": "The exact recorded conditions and evidence.",
            "reader_brief": {"why": "Apply the saved comparison to this case.", "concept": None,
                             "scope": "Only the supplied case is covered.", "next": "The recorded handoff remains pending."}}


def model():
    return MapModel("pi", "gpt-5.4-mini", "low", sys.executable)


def dataset():
    return {"id": "live:s-application", "tasks": [{"id": "a", "title": "Actual task", "objective": "Compare coverage",
                                                 "status": "running", "revision": "r1"}],
            "events": [{"id": "e", "item_id": "a", "type": "round.start", "text": "Recorded work",
                        "next_action": "A recorded handoff", "ts": 1, "revision": "e1"}]}


def run_stub(calls):
    def run(prompt, schema, config, **kwargs):
        sources = json.loads(prompt.rsplit("Retained sources:\n", 1)[1])
        saved = json.loads(prompt.split("Saved question foundation:\n", 1)[1].split("\nRetained sources:\n", 1)[0])
        calls.append({"prompt": prompt, "sources": sources, "foundation": saved, "schema": schema, "config": config, **kwargs})
        if kwargs.get("on_progress"):
            kwargs["on_progress"](kwargs["phase"])
        result = {"cards": {key: card() for key in sources["passages"]}, "relations": []}
        Draft202012Validator(schema).validate(result)
        return result
    return run


def test_application_uses_one_call_without_copying_foundation_and_sources_into_card(monkeypatch):
    docs = [{"key": "a", "task_id": "a", "task": {"title": "Actual task", "objective": "Compare coverage"},
             "events": [{"id": "e", "text": "x" * 1700, "next_action": "A saved handoff"}]}]
    tasks = [{"id": "a", "title": "Actual task"}, {"id": "b", "objective": "A neighboring goal"}]
    saved = foundation()
    original = copy.deepcopy((docs, tasks, saved))
    calls, phases = [], []
    monkeypatch.setattr(reader_application, "run_map_model", run_stub(calls))
    result = reader_application.generate_application(docs, tasks, "en-US", foundation=saved, config=model(),
                                                       project_root=None, global_root=None, on_progress=phases.append)
    assert len(calls) == 1 and calls[0]["run_label"] == "reader-application"
    assert calls[0]["prompt"].count(TEACHING_CORE) == 1
    assert calls[0]["config"] == model() and phases == ["writing"]
    generated = result["cards"][0]
    sent = calls[0]["sources"]["passages"]["a"]
    assert "source_snapshot" not in generated
    assert sent["events"][0]["text_truncated"] is True
    assert calls[0]["foundation"]["markdown"] == saved["markdown"]
    assert generated["foundation_ref"] == {key: saved[key] for key in ("id", "path", "question", "version")}
    assert generated["application_process"]["version"] == 1
    assert not {"learning_path", "teaching_review", "teaching_process"} & generated.keys()
    assert generated["reader_brief"]["concept"] is None
    assert generated["reader_brief"]["scope"] == card()["reader_brief"]["scope"]
    assert generated["reader_brief"]["next"] == card()["reader_brief"]["next"]
    assert (docs, tasks, saved) == original


@pytest.mark.parametrize("extra", ["learning_path", "concept"])
def test_application_rejects_new_lessons_even_from_a_nonconforming_runner(monkeypatch, extra):
    value = card()
    if extra == "learning_path":
        value[extra] = None
    else:
        value["reader_brief"]["concept"] = {"name": "Extra lesson", "explanation": "A definition",
                                               "example": "A new exercise", "connection": "A relation"}
    monkeypatch.setattr(reader_application, "run_map_model", lambda *a, **k: {"cards": {"a": value}, "relations": []})
    with pytest.raises(ValueError, match="cannot generate"):
        reader_application.generate_application([{"key": "a", "task_id": "a"}], [{"id": "a"}], "en-US",
                                                foundation=foundation(), config=model(), project_root=None, global_root=None)


def test_application_cache_is_question_specific_and_preserves_all_earlier_modes(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(map_narrative, "resolve_map_model", model)
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(reader_application, "run_map_model", run_stub(calls))
    monkeypatch.setattr(map_narrative, "generate", lambda *a, **k: pytest.fail("Application used the old teacher"))
    monkeypatch.setattr(map_lesson, "generate_source_first", lambda *a, **k: pytest.fail("Application generated an outline/lesson"))
    before = {}
    for preview in (False, True, "learning-path"):
        path = map_narrative.cache_path(tmp_path, map_narrative.copy_source(dataset()["id"], "en-US", preview=preview))
        path.parent.mkdir(exist_ok=True)
        path.write_text('{"cards":{"old":"retained"}}')
        before[path] = path.read_bytes()
    requests = [{"key": "a", "task_id": "a", "kind": "task", "event_ids": ["e"]}]

    def generate(saved, data=None):
        return map_narrative.enrich(tmp_path, data or dataset(), requests, "en-US", project_root=tmp_path,
                                    preview="question-foundation", foundation=saved)

    first = generate(foundation())
    assert not first["cached"] and first["version"] == 28 and first["process_version"] == 1
    assert generate(foundation())["cached"] is True and len(calls) == 1
    second = generate(foundation(id="different-question", path="reader-notes/different-question.md"))
    assert not second["cached"] and len(calls) == 2
    assert second["cards"]["a"]["foundation_ref"]["id"] == "different-question"
    assert first["cards"]["a"]["foundation_ref"]["id"] == "saved-question"
    assert all(path.read_bytes() == content for path, content in before.items())
    changed = dataset()
    changed["events"][0].update(text="New recorded progress", revision="e2")
    coalesced = generate(foundation(), changed)
    assert coalesced["retry_after"] == 25 and len(calls) == 2


def test_failed_application_keeps_prior_copy_without_source_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(map_narrative, "resolve_map_model", model)
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(reader_application, "run_map_model", run_stub([]))
    requests = [{"key": "a", "task_id": "a", "kind": "task", "event_ids": ["e"]}]
    kwargs = {"project_root": tmp_path, "preview": "question-foundation", "foundation": foundation()}
    first = map_narrative.enrich(tmp_path, dataset(), requests, "en-US", **kwargs)
    source = map_narrative.copy_source(dataset()["id"], "en-US", preview="question-foundation", foundation_id="saved-question")
    path = map_narrative.cache_path(tmp_path, source)
    old = map_narrative.read_cache(tmp_path, source)
    old["attempt_at"] = 0
    path.write_text(json.dumps(old))
    changed = dataset()
    changed["events"][0].update(text="A new observation", revision="new")

    def fail(*args, **kwargs):
        raise OSError("offline application failure")

    monkeypatch.setattr(reader_application, "run_map_model", fail)
    failure = map_narrative.enrich(tmp_path, changed, requests, "en-US", **kwargs)
    assert failure["generation_error"]["code"] == "provider_error"
    assert failure["retry_after"] > 0
    retained = map_narrative.read_cache(tmp_path, source)
    assert retained["cards"] == first["cards"]
    assert retained["cache_revision"] == first["cache_revision"]
    assert "source_snapshot" not in retained["cards"]["a"]


@pytest.mark.parametrize("neighbor_id", ["b", "🧪" * 161], ids=["ordinary-id", "bounded-id"])
def test_application_reuses_copy_when_only_neighbor_changes(
    tmp_path, monkeypatch, neighbor_id,
):
    now, calls = [1000.0], []
    monkeypatch.setattr(map_narrative, "resolve_map_model", model)
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative.time, "time", lambda: now[0])
    monkeypatch.setattr(reader_application, "run_map_model", run_stub(calls))
    data = dataset()
    data["tasks"][0]["deps"] = [neighbor_id]
    data["tasks"].append({"id": neighbor_id, "title": "Neighbor", "objective": "Old neighboring goal",
                          "status": "pending", "revision": "b1"})
    own_task = copy.deepcopy(data["tasks"][0])
    requests = [{"key": "a", "task_id": "a", "kind": "task", "event_ids": ["e"]}]

    def generate():
        return map_narrative.enrich(tmp_path, data, requests, "en-US", project_root=tmp_path,
                                    preview="question-foundation", foundation=foundation())

    saved = copy.deepcopy(generate()["cards"]["a"])
    assert calls[0]["sources"]["related_tasks"][0]["id"] == neighbor_id[:160]
    assert "source_snapshot" not in saved
    assert generate()["cached"] is True and len(calls) == 1
    data["tasks"][1]["objective"] = "A corrected neighboring goal"
    coalesced = generate()
    assert coalesced["cached"] is True and coalesced["cards"]["a"] == saved
    assert len(calls) == 1

    now[0] += 26
    refreshed = generate()
    assert refreshed["cached"] is True and len(calls) == 1
    current = refreshed["cards"]["a"]
    assert current == saved
    assert data["tasks"][0] == own_task
    assert generate()["cached"] is True and len(calls) == 1


@pytest.mark.parametrize("saved", [None, foundation(state="generating"), foundation(state="failed"), foundation(markdown="")])
def test_application_cannot_fall_back_to_a_teacher_without_a_complete_foundation(tmp_path, monkeypatch, saved):
    monkeypatch.setattr(map_narrative, "resolve_map_model", lambda: pytest.fail("An invalid foundation reached model setup"))
    with pytest.raises(ValueError, match="completed"):
        map_narrative.enrich(tmp_path, dataset(), [{"key": "a", "task_id": "a", "kind": "task", "event_ids": []}],
                            "en-US", project_root=tmp_path, preview="question-foundation", foundation=saved)
    assert not (tmp_path / "map-presentation").exists()


@pytest.fixture
def project(tmp_path):
    sid = "s-application"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_session_meta(tmp_path, SessionMeta(id=sid, created=1, last_active=1, workdir=str(workspace)))
    life = tmp_path / "projects" / sid
    LifeMemory.open(life).backlog.add(BacklogItem(id="a", ts=1, title="Actual task", objective="Compare coverage", status="pending"))
    return sid, life, workspace


def save_foundation(tmp_path, sid, *, state="complete", markdown=True):
    record, _ = reader_foundation.reserve_foundation(tmp_path, sid, request_id=str(uuid4()),
                                                     question="How is coverage compared?", locale="en-US")
    record["state"] = state
    reader_foundation._save_record(tmp_path / "projects" / sid, record)
    if markdown:
        from pathlib import Path

        path = Path(record["workspace"]) / record["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# The saved foundation\nIts original comparison rule.")
    return record


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("in_body", [False, True])
def test_application_route_uses_session_owned_artifact_and_shared_stream(project, tmp_path, monkeypatch, stream, in_body):
    sid, life, _ = project
    saved = save_foundation(tmp_path, sid)
    calls = []
    monkeypatch.setattr(map_narrative, "resolve_map_model", model)
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(reader_application, "run_map_model", run_stub(calls))
    client = TestClient(create_app(global_root=tmp_path, auth_token="test"))
    path = f"/api/map-copy/project/{sid}"
    params = {"preview": "question-foundation", "stream": str(stream).lower()}
    body = {"cards": [{"key": "a", "task_id": "a", "kind": "task"}], "locale": "en-US"}
    (body if in_body else params)["foundation_id"] = saved["id"]
    assert client.post(path, params=params, json=body).status_code == 401
    headers = {"Authorization": "Bearer test"}
    response = client.post(path, params=params, json=body, headers=headers)
    assert response.status_code == 200
    if stream:
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert frames[0]["type"] == "heartbeat"
        assert [frame["phase"] for frame in frames if frame["type"] == "progress"] == ["waiting_for_source", "writing"]
        assert sum(frame["type"] == "done" for frame in frames) == 1
        result = frames[-1]["result"]
    else:
        result = response.json()
    assert result["version"] == 28 and result["cards"]["a"]["foundation_ref"]["id"] == saved["id"]
    assert len(calls) == 1 and calls[0]["project_root"] == life
    assert calls[0]["foundation"]["markdown"].startswith("# The saved foundation")
    cached = client.get(path, params={"preview": "question-foundation", "foundation_id": saved["id"].upper(), "locale": "en-US"}, headers=headers).json()
    assert cached["cards"] == result["cards"] and cached["available"] and len(calls) == 1


@pytest.mark.parametrize("case,status", [("missing", 422), ("unknown", 404), ("generating", 422), ("failed", 422), ("unreadable", 422), ("foreign", 404)])
def test_application_get_is_empty_and_post_refuses_missing_or_incomplete_foundation(project, tmp_path, monkeypatch, case, status):
    sid, _, workspace = project
    params = {"preview": "question-foundation", "stream": "true"}
    if case == "unknown":
        params["foundation_id"] = str(uuid4())
    elif case != "missing":
        owner = sid
        if case == "foreign":
            owner = "s-other"
            write_session_meta(tmp_path, SessionMeta(id=owner, created=1, last_active=1, workdir=str(workspace)))
        saved = save_foundation(tmp_path, owner, state=case if case in {"generating", "failed"} else "complete",
                                markdown=case != "unreadable")
        params["foundation_id"] = saved["id"]
    monkeypatch.setattr(map_narrative, "enrich", lambda *a, **k: pytest.fail("Invalid foundation generated an application"))
    monkeypatch.setattr(map_narrative, "read_cache", lambda *a, **k: pytest.fail("Invalid foundation borrowed a cache"))
    client = TestClient(create_app(global_root=tmp_path))
    path = f"/api/map-copy/project/{sid}"
    result = client.get(path, params=params).json()
    assert result["version"] == 28 and result["available"] is False and result["cards"] == {} and result["relations"] == []
    response = client.post(path, params=params, json={"cards": [{"key": "a", "task_id": "a", "kind": "task"}]})
    assert response.status_code == status and "text/event-stream" not in response.headers["content-type"]


def test_application_rejects_mismatched_owner_ids_and_dataset_sources(project, tmp_path, monkeypatch):
    sid, _, _ = project
    saved = save_foundation(tmp_path, sid)
    folder = tmp_path / "datasets"
    folder.mkdir()
    (folder / "history.json").write_text(json.dumps({**dataset(), "id": "history", "read_only": True}))
    monkeypatch.setenv("ARGUS_MAP_DATASETS_DIR", str(folder))
    monkeypatch.setattr(map_narrative, "enrich", lambda *a, **k: pytest.fail("Invalid binding generated"))
    client = TestClient(create_app(global_root=tmp_path))
    params = {"preview": "question-foundation", "foundation_id": saved["id"]}
    body = {"cards": [{"key": "a", "task_id": "a", "kind": "task"}]}
    path = f"/api/map-copy/project/{sid}"
    assert client.post(path, params={**params, "session_id": "another"}, json=body).status_code == 422
    assert client.post(path, params=params, json={**body, "foundation_id": str(uuid4())}).status_code == 422
    assert client.get("/api/map-copy/dataset/history", params={**params, "session_id": sid}).status_code == 422
    assert client.post("/api/map-copy/dataset/history", params={**params, "session_id": sid}, json=body).status_code == 422


def test_application_cannot_select_a_clarification_as_the_root_foundation(project, tmp_path, monkeypatch):
    sid, life, workspace = project
    root = save_foundation(tmp_path, sid)
    child, _ = reader_foundation.reserve_foundation(tmp_path, sid, request_id=str(uuid4()),
        question="A follow-up doubt", locale="en-US", parent_id=root["id"])
    child["state"] = "complete"
    reader_foundation._save_record(life, child)
    (workspace / child["path"]).write_text("# Answer\nA saved clarification.")
    monkeypatch.setattr(map_narrative, "enrich", lambda *a, **k: pytest.fail("Clarification used as root for generation"))
    monkeypatch.setattr(map_narrative, "read_cache", lambda *a, **k: pytest.fail("Clarification borrowed a root cache"))
    client = TestClient(create_app(global_root=tmp_path))
    path = f"/api/map-copy/project/{sid}"
    params = {"preview": "question-foundation", "foundation_id": child["id"]}
    result = client.get(path, params=params).json()
    assert result["available"] is False and result["cards"] == {}
    response = client.post(path, params=params, json={"cards": [{"key": "a", "task_id": "a", "kind": "task"}]})
    assert response.status_code == 422
    with pytest.raises(ValueError, match="root foundation"):
        reader_application.foundation_reference(foundation(kind="clarification", parent_id="root", root_id="root"))
