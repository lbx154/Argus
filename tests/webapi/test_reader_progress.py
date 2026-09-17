"""Offline progress-source binding checks; fixture prose is not training data."""

from __future__ import annotations

import copy
import json
import sqlite3
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import Backlog, BacklogItem
from argus.webapi import map_narrative, reader_foundation, reader_progress
from argus.webapi.map_history import history_path, indexed_evidence
from argus.webapi.map_view import with_revisions
from argus.webapi.reader_clarification import ReaderSourceUnavailable
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
    monkeypatch.setattr("argus.webapi.map_history.indexed_evidence", lambda *a, **k: [])
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
        locale="en-US", **kwargs,
    )


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


def test_retained_generation_deduplicates_without_mutating_or_replacing_old_source(project, tmp_path, monkeypatch):
    sid, life, workspace = project
    card = selected_card()
    original = copy.deepcopy(card)
    ref = retain(tmp_path, sid, card)
    assert str(UUID(ref["source_id"])) == ref["source_id"]
    manifest = life / reader_progress.DIRECTORY / (ref["source_id"] + ".json")
    artifact = workspace / ref["path"]
    saved_bytes = manifest.read_bytes(), artifact.read_bytes()
    with monkeypatch.context() as repeat:
        repeat.setattr(reader_progress, "exclusive_file_lock", lambda *a, **k: pytest.fail("An unchanged source took the registry write lock"))
        repeat.setattr(reader_progress, "_resolved_evidence", lambda *a, **k: pytest.fail("An unchanged source resolved current evidence again"))
        assert retain(tmp_path, sid, card) == ref
    assert card == original
    changed = copy.deepcopy(card)
    changed.update(copy_revision=3, generated_at=200, detail="NEW OUTPUT FOR THE SAME TASK")
    changed["source_snapshot"]["events"][0]["text"] = "NEW SOURCE EXCERPT"
    newer = retain(tmp_path, sid, changed)
    assert newer["source_id"] != ref["source_id"]
    assert (manifest.read_bytes(), artifact.read_bytes()) == saved_bytes
    retained = reader_progress.read_progress_source(tmp_path, sid, ref["source_id"])
    assert retained["card"] == {key: value for key, value in original.items() if key != "source_snapshot"}
    assert retained["source_snapshot"] == original["source_snapshot"]
    assert "NEW OUTPUT" not in artifact.read_text()


def test_full_evidence_is_separate_and_only_matches_the_exact_record_revision(project, tmp_path):
    sid, _, workspace = project
    card = selected_card()
    engineer = {**card["source_snapshot"]["events"][0], "text": "THE RETAINED EXCERPT plus the full suffix"}
    engineer.pop("text_truncated")
    newer_review = {**card["source_snapshot"]["events"][1], "revision": "review-r3", "text": "NEWER REVIEW"}
    full_task = {"id": "task-one", "revision": "task-r2", "content_revision": "task-content-r2",
                 "objective": "Same-version complete task"}
    ref = retain(tmp_path, sid, card, evidence={"events": [engineer, newer_review], "tasks": [full_task]})
    record = reader_progress.read_progress_source(tmp_path, sid, ref["source_id"])
    assert record["source_snapshot"] == card["source_snapshot"]
    matches = {row["id"]: row for row in record["resolved_evidence"]["events"]}
    assert matches["engineer-event"]["state"] == "same_revision"
    assert matches["engineer-event"]["record"] == engineer
    assert matches["review-event"]["state"] == "unavailable"
    assert "record" not in matches["review-event"]
    assert record["resolved_evidence"]["task"]["record"] == full_task
    assert "NEWER REVIEW" not in json.dumps(reader_progress.source_context(record))
    document = (workspace / ref["path"]).read_text()
    assert document == record["document"]
    assert "SELECTED OUTPUT" in document
    assert "THE RETAINED EXCERPT" in document and "plus the full suffix" in document
    assert "engineer" in document.lower() and "reviewer" in document.lower()
    assert "00:02:01" in document and "00:02:02" in document
    assert "truncat" in document.lower()
    assert "```json" not in document and '"resolved_evidence":' not in document


@pytest.mark.parametrize("same_revision", [True, False])
def test_full_record_resolution_reuses_readonly_history_index_without_replacing_excerpt(project, tmp_path, monkeypatch, same_revision):
    sid, life, _ = project
    full = {"id": "engineer-event", "item_id": "task-one", "type": "round.main.completed", "role": "engineer",
            "ts": 121, "text": "a" * 1600 + " No Lean formalization."}
    original = with_revisions({"events": [full]})["events"][0]
    card = selected_card()
    card["source_snapshot"]["events"] = [{**original, "text": original["text"][:1600], "text_truncated": True}]
    card["source_snapshot"]["source_ids"] = [original["id"]]
    saved = full if same_revision else {**full, "text": "A later different record."}
    path = history_path(tmp_path, life)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, id TEXT UNIQUE, body TEXT)")
        db.execute("INSERT INTO events VALUES (1, ?, ?)", (full["id"], json.dumps(saved)))
    monkeypatch.setattr("argus.webapi.map_history.indexed_evidence", indexed_evidence)
    ref = retain(tmp_path, sid, card)
    record = reader_progress.read_progress_source(tmp_path, sid, ref["source_id"])
    assert record["source_snapshot"] == card["source_snapshot"]
    resolved, = record["resolved_evidence"]["events"]
    assert resolved["state"] == ("same_revision" if same_revision else "unavailable")
    if same_revision:
        assert resolved["record"] == original and "No Lean formalization." in record["document"]
    else:
        assert "record" not in resolved and "A later different record." not in record["document"]


@pytest.mark.parametrize("retention_fails", [False, True])
def test_enrichment_retains_old_version_before_replacing_same_card(project, tmp_path, monkeypatch, retention_fails):
    sid, life, _ = project
    old = selected_card()
    source = map_narrative.copy_source(f"live:{sid}", "en-US")
    path = map_narrative.cache_path(tmp_path, source)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"cards": {"task-one": old}, "cache_revision": old["copy_revision"], "attempt_at": 0}))
    newer_event = {**old["source_snapshot"]["events"][0], "text": "NEW SOURCE MATERIAL", "revision": "new-source-revision"}
    dataset = {"id": f"live:{sid}", "tasks": [{"id": "task-one", "title": "Current task", "objective": "Existing goal", "status": "done"}],
               "events": [newer_event]}

    def generate(documents, tasks, locale, **kwargs):
        document, = documents
        new = copy.deepcopy(old)
        new["key"] = "task-one"
        new["detail"] = "NEW EXPLANATION FOR THIS CARD"
        new["source_snapshot"] = {"version": 2, "card_key": "task-one", "task_id": "task-one", "captured_at": 200,
                                  "task": document["task"], "events": document["events"], "source_ids": [newer_event["id"]]}
        return {"cards": [new], "relations": []}

    calls = []
    original_retain = reader_progress.retain_progress_source

    def retaining(*args, **kwargs):
        calls.append(kwargs["card"]["detail"])
        if retention_fails:
            raise OSError("Synthetic source storage failure")
        return original_retain(*args, **kwargs)

    monkeypatch.setattr(map_narrative, "generate", generate)
    monkeypatch.setattr(reader_progress, "retain_progress_source", retaining)
    request = [{"key": "task-one", "task_id": "task-one", "kind": "task", "event_ids": [newer_event["id"]]}]
    if retention_fails:
        with pytest.raises(OSError, match="Synthetic"):
            map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=life)
        assert calls == [old["detail"]]
        assert map_narrative.read_cache(tmp_path, source)["cards"] == {"task-one": old}
    else:
        result = map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=life)
        assert calls == [old["detail"], "NEW EXPLANATION FOR THIS CARD"]
        newest = result["cards"]["task-one"]["progress_source"]
        records = [reader_progress.read_progress_source(tmp_path, sid, entry.stem)
                   for entry in (life / reader_progress.DIRECTORY).glob("*.json") if entry.name != "index.json"]
        prior, = [row for row in records if row["id"] != newest["source_id"]]
        assert prior["card"]["detail"] == old["detail"] and prior["source_snapshot"] == old["source_snapshot"]
        assert reader_progress.read_progress_source(tmp_path, sid, newest["source_id"])["card"]["detail"] == "NEW EXPLANATION FOR THIS CARD"
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


@pytest.mark.parametrize("missing", ["source_snapshot", "copy_revision", "generated_at"])
def test_an_unresolvable_legacy_card_is_not_reconstructed_from_foundation(project, tmp_path, missing):
    sid, life, _ = project
    card = selected_card()
    card.pop(missing)
    with pytest.raises(ReaderSourceUnavailable):
        retain(tmp_path, sid, card)
    assert not (life / reader_progress.DIRECTORY).exists()


def test_legacy_map_get_registers_displayed_copy_without_mutating_cache_or_waiting_for_generation(project, tmp_path, monkeypatch):
    sid, _, workspace = project
    cache = {"cards": {"task-one": selected_card()}, "relations": [], "cache_revision": 4}
    cache_path = tmp_path / "existing-map-copy.json"
    cache_path.write_text(json.dumps(cache))
    before = cache_path.read_bytes()
    monkeypatch.setattr(map_narrative, "cache_path", lambda *a: cache_path)
    monkeypatch.setattr(map_narrative, "_source_lock", lambda *a: pytest.fail("GET waited for a generation lock"))
    monkeypatch.setattr(map_narrative, "enrich", lambda *a, **k: pytest.fail("GET generated a card"))
    client = TestClient(create_app(global_root=tmp_path))
    url = f"/api/map-copy/project/{sid}?locale=en-US"
    first = client.get(url)
    assert first.status_code == 200, first.text
    result = first.json()
    ref = result["cards"]["task-one"]["progress_source"]
    assert result["cache_revision"] == 4
    assert client.get(url).json()["cards"]["task-one"]["progress_source"] == ref
    assert cache_path.read_bytes() == before
    assert (workspace / ref["path"]).is_file()
    source = client.get(f"/api/projects/{sid}/artifact", params={"path": ref["path"]})
    assert source.status_code == 200, source.text
    assert source.json()["source"] == "progress_snapshot"
    raw = client.get(f"/api/projects/{sid}/artifact/raw", params={"path": ref["path"]})
    assert raw.status_code == 200
    assert raw.text == (workspace / ref["path"]).read_text()
    assert "SELECTED OUTPUT" in raw.text and "THE RETAINED EXCERPT" in raw.text
    assert client.get(f"/api/projects/{sid}/artifacts").json() == {"artifacts": []}


@pytest.mark.parametrize("stream", [False, True])
def test_first_progress_question_uses_exact_source_and_shared_artifact_request_lifecycle(project, tmp_path, monkeypatch, stream):
    sid, life, workspace = project
    card = selected_card()
    ref = retain(tmp_path, sid, card)
    source_before = (workspace / ref["path"]).read_bytes()
    backlog_before = (life / "backlog.jsonl").read_bytes()
    for target in (
        "argus.webapi.manager_bridge.manager_message",
        "argus.apps._inbox.queue_inbox_message",
        "argus.core.transcript.append_turn",
    ):
        monkeypatch.setattr(target, lambda *a, **k: pytest.fail("Reader question entered the research pipeline"))
    calls = []
    monkeypatch.setattr(reader_foundation, "run_map_model", fake_runner(calls))
    client = TestClient(create_app(global_root=tmp_path))
    body = question(ref["source_id"])
    url = f"/api/projects/{sid}/reader-foundation"
    artifact = response_artifact(client.post(url, params={"stream": stream}, json=body), stream=stream)
    metadata = artifact["reader_foundation"]
    assert metadata["kind"] == "progress_answer"
    assert metadata["state"] == "complete"
    assert metadata["id"] == body["request_id"]
    saved = reader_foundation.read_foundation(tmp_path, sid, body["request_id"])
    bound = saved["source_snapshot"]["progress_source"]
    assert bound["source_id"] == ref["source_id"]
    assert bound["card"] == {key: value for key, value in card.items() if key != "source_snapshot"}
    assert bound["source_snapshot"] == card["source_snapshot"]
    assert saved["source_snapshot"]["sources"] == []
    prompt, _, options = calls[0]
    assert "SELECTED OUTPUT" in prompt and "BACKGROUND OUTPUT" in prompt
    assert "THE RETAINED EXCERPT" in prompt and "completion-event" in prompt
    assert body["question"] in prompt and "mission_id" not in options
    assert options["run_label"] == metadata["provenance"]["run_label"]
    assert (workspace / ref["path"]).read_bytes() == source_before
    assert (life / "backlog.jsonl").read_bytes() == backlog_before
    assert not (life / "events.jsonl").exists()
    raw = client.get(f"/api/projects/{sid}/artifact/raw", params={"path": artifact["path"]})
    assert raw.text == (workspace / artifact["path"]).read_text() == saved["markdown"]
    assert ref["path"] in raw.text
    assert raw.text.startswith("# Offline answer 1\n\nUnique body of offline answer number 1.\n\n---\n\n")
    assert raw.text.index(body["question"]) > raw.text.index("---")
    assert response_artifact(client.post(url, json=body)) == artifact
    assert client.post(url, json={**body, "question": "Changed question"}).status_code == 409
    newer = copy.deepcopy(card)
    newer.update(copy_revision=3, generated_at=200, detail="NEW COPY")
    alternate = retain(tmp_path, sid, newer)
    assert client.post(url, json={**body, "progress_source": {"source_id": alternate["source_id"]}}).status_code == 409
    assert len(calls) == 1


def test_progress_followups_keep_one_source_and_only_the_explicit_parent_answer(project, tmp_path, monkeypatch):
    sid, _, workspace = project
    ref = retain(tmp_path, sid, selected_card())
    calls = []
    monkeypatch.setattr(reader_foundation, "run_map_model", fake_runner(calls))
    client = TestClient(create_app(global_root=tmp_path))
    first_body = question(ref["source_id"])
    first = response_artifact(client.post(f"/api/projects/{sid}/reader-foundation", json=first_body))
    second_body = question(text="Explain one point in the first answer.")
    second = response_artifact(client.post(
        f"/api/projects/{sid}/reader-foundation/{first_body['request_id']}/question", json=second_body,
    ))
    third_body = question(text="Explain this point in the selected second answer.")
    reserved, created = reader_foundation.reserve_foundation(
        tmp_path, sid, **third_body, parent_id=second_body["request_id"],
    )
    assert created
    snapshot = reserved["source_snapshot"]
    assert snapshot["progress_source"]["source_id"] == ref["source_id"]
    assert [source["id"] for source in snapshot["sources"]] == [second_body["request_id"]]
    selected_parent_bytes = (workspace / second["path"]).read_bytes()
    assert snapshot["sources"][0]["markdown"].encode() == selected_parent_bytes
    (workspace / second["path"]).write_text("PARENT CHANGED AFTER RESERVATION")
    (workspace / first["path"]).write_text("FIRST ANSWER IS NOT THE SELECTED PARENT")
    third = reader_foundation.generate_foundation(tmp_path, sid, reserved)
    prompt = calls[-1][0]
    assert "Unique body of offline answer number 2." in prompt
    assert "Unique body of offline answer number 1." not in prompt
    assert "PARENT CHANGED AFTER RESERVATION" not in prompt
    assert "FIRST ANSWER IS NOT THE SELECTED PARENT" not in prompt
    assert third["reader_foundation"]["parent_id"] == second_body["request_id"]
    saved = reader_foundation.read_foundation(tmp_path, sid, third_body["request_id"])
    assert saved["source_snapshot"] == snapshot
    assert len(calls) == 3


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
    ref = retain(tmp_path, sid, selected_card())
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
