"""Offline progress-source binding checks; fixture prose is not training data."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import Backlog, BacklogItem
from argus.webapi import map_narrative, reader_foundation, reader_progress
from argus.webapi.server import create_app


@pytest.fixture
def project(tmp_path, monkeypatch):
    sid = "s-progress"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_session_meta(tmp_path, SessionMeta(id=sid, workdir=str(workspace)))
    life = tmp_path / "projects" / sid
    Backlog(life / "backlog.jsonl").add(BacklogItem(
        id="task-one", ts=1, title="Existing task", objective="Recorded comparison", status="done",
    ))
    model = SimpleNamespace(available=True, revision="offline-model")
    monkeypatch.setattr(reader_foundation, "resolve_map_model", lambda: model)
    monkeypatch.setattr(map_narrative, "resolve_map_model", lambda: model)
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    return sid, life, workspace


def selected_card():
    return {
        "title": "The selected recorded comparison",
        "summary": "Only the selected case is covered.",
        "detail": "SELECTED OUTPUT: A denotes this deployment, subject to the stated assumptions.",
        "reader_brief": {
            "why": "The reader selected this recorded step.", "scope": "One bounded case.",
            "next": "The historical task is done; no new task was assigned.", "concept": None,
        },
        "version": 23, "copy_revision": 2, "generated_at": 123.5,
        "input_revision": "saved-input", "task_revision": "task-r2",
        "task_content_revision": "task-content-r2", "model_revision": "offline-model",
        "event_ids": ["engineer-event", "review-event"],
        "event_revisions": ["engineer-r2", "review-r2"],
        "source_snapshot": {
            "version": 2, "card_key": "task-one", "task_id": "task-one", "captured_at": 120,
            "task": {
                "title": "The historical task", "attempt": 2, "status": "done",
                "outcome": {"execution_status": "completed", "review_status": "done",
                            "stage_certification": "deferred"},
                "outcome_source": {"event_id": "completion-event", "binding": "attempt_and_start"},
            },
            "events": [
                {"id": "engineer-event", "item_id": "task-one", "revision": "engineer-r2",
                 "type": "round.main.completed", "role": "engineer", "ts": 121,
                 "text": "THE RETAINED EXCERPT", "text_truncated": True},
                {"id": "review-event", "item_id": "task-one", "revision": "review-r2",
                 "type": "round.review.completed", "role": "reviewer", "ts": 122,
                 "text": "A separate bounded review.", "review_skipped": False},
            ],
            "source_ids": ["engineer-event", "review-event"],
            "related_tasks": [{"id": "neighbor", "objective": "Context, not a new assignment."}],
            "foundation": {"id": str(UUID(int=1)), "path": "reader-notes/s-progress/background.md",
                           "question": "A background question", "version": 1,
                           "markdown": "BACKGROUND OUTPUT: A denotes a collection in this example."},
        },
    }


def retain(tmp_path, sid, card, **kwargs):
    return reader_progress.retain_progress_source(
        tmp_path, sid, copy_source=f"live:{sid}:en-US", card_key="task-one", card=card,
        locale="en-US", records=kwargs.get("records", current_records()),
    )


def current_records():
    snapshot = selected_card()["source_snapshot"]
    return {"task_id": snapshot["task_id"], "task": snapshot["task"], "events": [
        {**event, "text": "QUESTION-TIME RECORD " + event["text"]} for event in snapshot["events"]
    ]}


def legacy_source(tmp_path, sid, life, workspace):
    ref = retain(tmp_path, sid, selected_card())
    path = life / reader_progress.DIRECTORY / (ref["source_id"] + ".json")
    record = json.loads(path.read_text())
    record.pop("records")
    record["source_snapshot"] = selected_card()["source_snapshot"]
    record["resolved_evidence"] = {"task": {"state": "unavailable"}, "events": []}
    record["document"] = "# Legacy explanation\n\nSELECTED OUTPUT\n\nTHE RETAINED EXCERPT\n"
    path.write_text(json.dumps(record))
    (workspace / ref["path"]).write_bytes(record["document"].encode())  # exact bytes on every OS
    return ref


def fake_runner(calls, *, fail=False):
    def run(prompt, schema, config, **kwargs):
        assert kwargs["output_format"] == "markdown"
        calls.append((prompt, schema, kwargs))
        if kwargs.get("on_progress"):
            kwargs["on_progress"]("writing")
        kwargs["on_result"](SimpleNamespace(
            call_id=f"offline-call-{len(calls)}", call_id_log_correlated=True, exit_code=0,
        ))
        if fail:
            raise ValueError("invalid generated schema")
        return {"title": f"Offline answer {len(calls)}",
                "markdown": f"# Offline answer {len(calls)}\n\nUnique body of offline answer number {len(calls)}."}
    return run


def question(source_id=None, text="Explain the selected comparison."):
    body = {"request_id": str(uuid4()), "question": text, "locale": "en-US"}
    if source_id is not None:
        body["progress_source"] = {"source_id": source_id}
    return body


def response_artifact(response, *, stream=False):
    assert response.status_code == 200, response.text
    if not stream:
        return response.json()
    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert sum(frame["type"] == "done" for frame in frames) == 1
    assert not any(frame["type"] == "error" for frame in frames)
    return next(frame["result"] for frame in frames if frame["type"] == "done")


def test_registered_source_is_session_owned_even_when_workspaces_are_shared(project, tmp_path):
    sid, _, workspace = project
    ref = retain(tmp_path, sid, selected_card())
    other = "s-other"
    write_session_meta(tmp_path, SessionMeta(id=other, workdir=str(workspace)))
    assert reader_progress.read_progress_source(tmp_path, other, ref["source_id"]) is None
    assert reader_progress.registered_progress_artifact(tmp_path, other, ref["path"]) is None
    alternate = retain(tmp_path, other, selected_card())
    assert alternate["source_id"] != ref["source_id"]
    assert alternate["path"] != ref["path"]
    assert (workspace / ref["path"]).is_file()


@pytest.mark.parametrize("invalid", [
    "unknown", "foreign", "missing_artifact", "modified_artifact", "empty_artifact",
    "client_card", "client_snapshot",
])
def test_new_progress_question_rejects_unavailable_or_client_supplied_sources_without_generation(project, tmp_path, monkeypatch, invalid):
    sid, life, workspace = project
    ref = retain(tmp_path, sid, selected_card())
    body = question(ref["source_id"])
    if invalid == "unknown":
        body["progress_source"]["source_id"] = str(uuid4())
    elif invalid == "foreign":
        other = "s-other"
        write_session_meta(tmp_path, SessionMeta(id=other, workdir=str(workspace)))
        body["progress_source"] = {"source_id": retain(tmp_path, other, selected_card())["source_id"]}
    elif invalid == "missing_artifact":
        (workspace / ref["path"]).unlink()
    elif invalid == "modified_artifact":
        (workspace / ref["path"]).write_text("Replaced after this source was registered.")
    elif invalid == "empty_artifact":
        (workspace / ref["path"]).write_text("\n")
    elif invalid == "client_card":
        body["progress_source"]["card"] = selected_card()
    else:
        body["source_snapshot"] = selected_card()["source_snapshot"]
    monkeypatch.setattr(reader_foundation, "run_map_model", lambda *a, **k: pytest.fail("Unavailable source generated"))
    client = TestClient(create_app(global_root=tmp_path))
    if invalid in {"missing_artifact", "modified_artifact", "empty_artifact"}:
        for suffix in ("/artifact", "/artifact/raw"):
            read = client.get(f"/api/projects/{sid}{suffix}", params={"path": ref["path"]})
            assert read.status_code == 404
            assert "Replaced after this source was registered." not in read.text
    response = client.post(f"/api/projects/{sid}/reader-foundation", json=body)
    assert response.status_code == 422, response.text
    if invalid in {"unknown", "foreign", "missing_artifact", "modified_artifact", "empty_artifact"}:
        assert response.json()["detail"]["code"] == "reader_source_unavailable"
    assert not (life / reader_foundation.MANIFEST_DIRECTORY / (body["request_id"] + ".json")).exists()


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("legacy_version", [1, 2])
def test_existing_progress_answer_replays_and_selected_parent_keeps_its_source_after_source_file_loss(project, tmp_path, monkeypatch, failed, legacy_version):
    sid, life, workspace = project
    assert reader_foundation.FOUNDATION_VERSION == 3
    ref = legacy_source(tmp_path, sid, life, workspace)
    calls = []
    monkeypatch.setattr(reader_foundation, "run_map_model", fake_runner(calls))
    client = TestClient(create_app(global_root=tmp_path))
    url = f"/api/projects/{sid}/reader-foundation"
    first_body = question(ref["source_id"])
    with monkeypatch.context() as legacy:
        legacy.setattr(reader_foundation, "FOUNDATION_VERSION", legacy_version)
        legacy.setattr(reader_foundation, "run_map_model", fake_runner(calls, fail=failed))
        initial = client.post(url, json=first_body)
    assert initial.status_code == (422 if failed else 200)
    old = reader_foundation.read_foundation(tmp_path, sid, first_body["request_id"])
    first = reader_foundation.foundation_artifact(old)
    assert old["version"] == legacy_version and old["state"] == ("failed" if failed else "complete")
    bound = copy.deepcopy(old["source_snapshot"]["progress_source"])
    manifest = life / reader_foundation.MANIFEST_DIRECTORY / (first_body["request_id"] + ".json")
    before = manifest.read_bytes()
    (workspace / ref["path"]).unlink()
    with monkeypatch.context() as replay:
        replay.setattr(reader_foundation, "generate_foundation", lambda *a, **k: pytest.fail("Old progress answer was readmitted"))
        replay.setattr(reader_progress, "progress_question_sources", lambda *a, **k: pytest.fail("Replay reread its progress source"))
        assert response_artifact(client.post(url, json=first_body)) == first
    assert manifest.read_bytes() == before
    assert len(calls) == 1
    if failed:
        new_body = question(ref["source_id"])
        rejected = client.post(url, json=new_body)
        assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "reader_source_unavailable"
        assert not (life / reader_foundation.MANIFEST_DIRECTORY / (new_body["request_id"] + ".json")).exists()
        assert len(calls) == 1
        return
    followup_body = question(text="One doubt in this saved answer.")
    followup_url = f"{url}/{first_body['request_id']}/question"
    response_artifact(client.post(followup_url, json=followup_body))
    saved = reader_foundation.read_foundation(tmp_path, sid, followup_body["request_id"])
    assert saved["version"] == 3
    assert saved["source_snapshot"]["progress_source"] == bound
    assert [source["id"] for source in saved["source_snapshot"]["sources"]] == [first_body["request_id"]]
    assert saved["source_snapshot"]["sources"][0]["version"] == legacy_version
    assert saved["source_snapshot"]["sources"][0]["markdown"] == old["markdown"]
    assert manifest.read_bytes() == before
    assert len(calls) == 2
    (workspace / first["path"]).unlink()
    rejected = client.post(followup_url, json=question(text="A new question needs a readable selected answer."))
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "reader_source_unavailable"
    assert len(calls) == 2
