"""Persistent explicit reading requests, using only local fake runner results."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from argus_skill.core.mission_view import update_mission_view_event
from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.life.memory import Backlog, BacklogItem
from argus_skill.webapi import reader_foundation as foundation
from argus_skill.webapi.artifacts import get_project_artifact, list_project_artifacts
from argus_skill.webapi.server import create_app


@pytest.fixture
def project(tmp_path, monkeypatch):
    sid = "s-foundation"
    life = tmp_path / "projects" / sid
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_session_meta(tmp_path, SessionMeta(id=sid, workdir=str(workspace)))
    Backlog(life / "backlog.jsonl").add(BacklogItem(
        id="task-one", ts=1, title="Existing research", objective="An actual task", status="pending",
    ))
    monkeypatch.setattr(foundation, "resolve_map_model", lambda: SimpleNamespace(available=True))
    body = {
        "request_id": str(uuid4()), "question": "Explain why a feasible bound can certify optimality.",
        "locale": "en-US", "source_task_id": "task-one",
    }
    return sid, life, workspace, body


def fake_run(calls, **options):
    def run(prompt, schema, config, **kwargs):
        assert kwargs["output_format"] == "markdown"
        calls.append((prompt, schema, kwargs))
        if kwargs.get("on_progress"):
            kwargs["on_progress"]("writing")
        kwargs["on_result"](SimpleNamespace(
            call_id="native-runtime-call", call_id_log_correlated=True, exit_code=0,
        ))
        if options.get("fail"):
            raise ValueError("invalid generated schema")
        return {"title": "Feasible bounds", "markdown": "# Feasible bounds\n\nA lower bound and a feasible upper bound can meet."}
    return run


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_explicit_request_reuses_artifacts_and_native_capture_receipt(project, tmp_path, monkeypatch, stream, locale):
    sid, life, workspace, body = project
    body = {**body, "locale": locale}
    calls = []
    monkeypatch.setattr(foundation, "run_map_model", fake_run(calls))
    client = TestClient(create_app(global_root=tmp_path))
    url = f"/api/projects/{sid}/reader-foundation"
    response = client.post(url, params={"stream": stream}, json=body)
    assert response.status_code == 200
    if stream:
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert [frame["type"] for frame in frames] == ["heartbeat", "progress", "done"]
        assert frames[1]["phase"] == "writing"
        artifact = frames[-1]["result"]
    else:
        artifact = response.json()
    metadata = artifact["reader_foundation"]
    assert artifact["source"] == "reader_foundation" and artifact["exists"] is True
    assert metadata["state"] == "complete" and metadata["id"] == body["request_id"]
    assert metadata["provenance"]["call_id"] == "native-runtime-call"
    assert metadata["provenance"]["call_id_log_correlated"] is True
    assert metadata["provenance"]["origin"] == "explicit_user_request"
    assert metadata["version"] == 3
    prompt, _, options = calls[0]
    assert body["question"] in prompt
    assert "task-one" not in prompt
    assert options["run_label"] == "reader-foundation"
    assert options["project_root"] == life and options["global_root"] == tmp_path
    assert "mission_id" not in options
    assert client.get(f"/api/projects/{sid}/artifacts").json() == {"artifacts": []}
    listed = client.get(f"/api/projects/{sid}/artifacts?include_reading=true").json()["artifacts"]
    assert listed == [artifact]
    detail = client.get(f"/api/projects/{sid}/artifact", params={"path": artifact["path"]})
    assert "# Feasible bounds" in detail.json()["preview"]
    assert body["question"] in detail.json()["preview"]
    assert ("背景说明" if locale == "zh-CN" else "Background reading") in detail.json()["preview"]
    assert body["request_id"] not in detail.json()["preview"]
    assert client.get(f"/api/projects/{sid}/artifact/raw", params={"path": artifact["path"]}).text == detail.json()["preview"]
    assert (workspace / artifact["path"]).read_text() == detail.json()["preview"]
    native = "# Feasible bounds\n\nA lower bound and a feasible upper bound can meet."
    assert detail.json()["preview"].startswith(native + "\n\n---\n\n")
    assert detail.json()["preview"].index(body["question"]) > len(native)
    assert client.post(url, json=body).json() == artifact
    assert client.post(url, json={**body, "question": "A different question"}).status_code == 409
    assert len(calls) == 1
    assert not (life / "events.jsonl").exists()


def test_fixed_workspace_survives_campaign_change_and_registration_remains_session_owned(project, tmp_path, monkeypatch):
    sid, life, workspace, body = project
    calls = []
    monkeypatch.setattr(foundation, "run_map_model", fake_run(calls))
    client = TestClient(create_app(global_root=tmp_path))
    artifact = client.post(f"/api/projects/{sid}/reader-foundation", json=body).json()
    alternate = tmp_path / "new-campaign"
    alternate.mkdir()
    (alternate / artifact["path"]).parent.mkdir(parents=True)
    (alternate / artifact["path"]).write_text("OTHER CAMPAIGN CONTENT")
    monkeypatch.setattr("argus_skill.webapi.artifacts._effective_workspace", lambda *_: alternate)
    assert client.get(f"/api/projects/{sid}/artifact/raw", params={"path": artifact["path"]}).text.startswith("# Feasible")
    record = foundation.read_foundation(tmp_path, sid, body["request_id"])
    assert record["workspace"] == str(workspace) and record["markdown"].startswith("# Feasible")
    other = "s-other"
    write_session_meta(tmp_path, SessionMeta(id=other, workdir=str(workspace)))
    assert get_project_artifact(other, artifact["path"], global_root=tmp_path) is None
    update_mission_view_event(life, {
        "type": "life.mission.completed", "item_id": "task-one", "success": True,
        "delivery": {"targets": [{"path": artifact["path"]}]},
    })
    assert list_project_artifacts(sid, global_root=tmp_path) == []
    unregistered = f"reader-notes/{sid}/{uuid4()}.md"
    (alternate / unregistered).write_text("Unregistered")
    assert get_project_artifact(sid, unregistered, global_root=tmp_path) is None
    assert client.get(f"/api/projects/{sid}/artifact/raw", params={"path": "../escape.md"}).status_code == 404
    assert len(calls) == 1


def test_failed_parse_keeps_real_call_and_never_retries_on_read_or_repost(project, tmp_path, monkeypatch):
    sid, _, workspace, body = project
    calls = []
    monkeypatch.setattr(foundation, "run_map_model", fake_run(calls, fail=True))
    client = TestClient(create_app(global_root=tmp_path))
    url = f"/api/projects/{sid}/reader-foundation"
    assert client.post(url, json=body).status_code == 422
    result = client.get(f"/api/projects/{sid}/artifacts?include_reading=true").json()["artifacts"][0]
    assert result["exists"] is False
    assert result["reader_foundation"]["state"] == "failed"
    assert result["reader_foundation"]["provenance"]["call_id"] == "native-runtime-call"
    assert not (workspace / result["path"]).exists()
    assert client.post(url, json=body).json() == result
    assert client.get(f"/api/projects/{sid}/artifact", params={"path": result["path"]}).status_code == 404
    assert len(calls) == 1


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("legacy_version", [1, 2])
def test_version_upgrade_preserves_terminal_request_and_only_versions_new_ids(project, tmp_path, monkeypatch, failed, legacy_version):
    sid, life, _, body = project
    assert foundation.FOUNDATION_VERSION == 3
    calls = []
    monkeypatch.setattr(foundation, "run_map_model", fake_run(calls))
    client = TestClient(create_app(global_root=tmp_path))
    url = f"/api/projects/{sid}/reader-foundation"
    with monkeypatch.context() as legacy:
        legacy.setattr(foundation, "FOUNDATION_VERSION", legacy_version)
        legacy.setattr(foundation, "run_map_model", fake_run(calls, fail=failed))
        initial = client.post(url, json=body)
    assert initial.status_code == (422 if failed else 200)
    saved = foundation.read_foundation(tmp_path, sid, body["request_id"])
    retained = foundation.foundation_artifact(saved)
    assert saved["version"] == legacy_version and saved["state"] == ("failed" if failed else "complete")
    manifest = life / foundation.MANIFEST_DIRECTORY / (body["request_id"] + ".json")
    before = manifest.read_bytes()
    with monkeypatch.context() as replay:
        replay.setattr(foundation, "generate_foundation", lambda *a, **k: pytest.fail("Old request was readmitted"))
        replay.setattr(foundation, "Backlog", lambda *a, **k: pytest.fail("Replay reread its source task"))
        replay.setattr("argus_skill.webapi.artifacts.artifact_workspace", lambda *a, **k: pytest.fail("Replay reserved a new workspace"))
        assert client.post(url, json=body).json() == retained
    assert manifest.read_bytes() == before and len(calls) == 1
    new_body = {**body, "request_id": str(uuid4())}
    new = client.post(url, json=new_body)
    assert new.status_code == 200
    assert new.json()["reader_foundation"]["version"] == 3
    assert len(calls) == 2 and manifest.read_bytes() == before


def test_concurrent_same_request_and_refresh_share_one_persistent_generation(project, tmp_path, monkeypatch):
    sid, _, _, body = project
    started, release = threading.Event(), threading.Event()
    calls = []
    runner = fake_run(calls)

    def blocking_run(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return runner(*args, **kwargs)

    monkeypatch.setattr(foundation, "run_map_model", blocking_run)
    client = TestClient(create_app(global_root=tmp_path))
    url = f"/api/projects/{sid}/reader-foundation"
    with ThreadPoolExecutor() as executor:
        first = executor.submit(client.post, url, json=body)
        try:
            assert started.wait(5)
            duplicate = client.post(url, json=body).json()
            assert duplicate["reader_foundation"]["state"] == "generating"
            assert duplicate["exists"] is False
            assert client.get(f"/api/projects/{sid}/artifacts?include_reading=true").json()["artifacts"] == [duplicate]
        finally:
            release.set()
        assert first.result().json()["reader_foundation"]["state"] == "complete"
    assert len(calls) == 1


def test_live_owner_past_deadline_stays_generating_without_a_read_mutation(project, tmp_path, monkeypatch):
    sid, life, _, body = project
    record, created = foundation.reserve_foundation(tmp_path, sid, **body)
    assert created
    record["deadline_at"] = time.time() - 1
    foundation._save_record(life, record)
    path = life / foundation.MANIFEST_DIRECTORY / (body["request_id"] + ".json")
    before = path.read_bytes()
    monkeypatch.setattr(foundation, "run_map_model", lambda *a, **kw: pytest.fail("A read must never generate"))
    result = foundation.read_foundation(tmp_path, sid, body["request_id"])
    assert result["state"] == "generating" and result["deadline_exceeded"] is True
    assert "error" not in result
    assert path.read_bytes() == before
    assert foundation.reserve_foundation(tmp_path, sid, **body)[1] is False


@pytest.mark.parametrize("reused_pid", [False, True])
def test_dead_or_reused_owner_pid_is_failed_without_regenerating(project, tmp_path, monkeypatch, reused_pid):
    from argus_skill.core import process_identity

    sid, life, _, body = project
    record, _ = foundation.reserve_foundation(tmp_path, sid, **body)
    if reused_pid:
        record["owner"]["start_time_ticks"] = "older-process-start"
        monkeypatch.setattr(process_identity, "read_process_start_ticks", lambda *a, **kw: "new-process-start")
    else:
        record["owner"] = {"pid": 0}
    foundation._save_record(life, record)
    manifest = life / foundation.MANIFEST_DIRECTORY / (body["request_id"] + ".json")
    before = manifest.read_bytes()
    result = foundation.read_foundation(tmp_path, sid, body["request_id"])
    assert result["state"] == "failed" and result["error"] == "generation_owner_terminated"
    assert manifest.read_bytes() == before
    assert foundation.reserve_foundation(tmp_path, sid, **body)[1] is False


def test_shared_workspace_and_same_uuid_do_not_overwrite_another_sessions_foundation(project, tmp_path, monkeypatch):
    sid, _, workspace, body = project
    calls = []
    monkeypatch.setattr(foundation, "run_map_model", fake_run(calls))
    client = TestClient(create_app(global_root=tmp_path))
    first = client.post(f"/api/projects/{sid}/reader-foundation", json=body).json()
    original = (workspace / first["path"]).read_text()
    other_sid = "s-other"
    write_session_meta(tmp_path, SessionMeta(id=other_sid, workdir=str(workspace)))
    other_body = {**body, "question": "A separate real question in another session.", "source_task_id": None}
    second = client.post(f"/api/projects/{other_sid}/reader-foundation", json=other_body).json()
    assert first["path"] != second["path"]
    assert (workspace / first["path"]).read_text() == original
    assert other_body["question"] in (workspace / second["path"]).read_text()
    assert get_project_artifact(sid, second["path"], global_root=tmp_path) is None
    assert get_project_artifact(other_sid, first["path"], global_root=tmp_path) is None
    assert len(calls) == 2


def test_late_runner_result_keeps_receipt_without_publishing_past_the_deadline(project, tmp_path, monkeypatch):
    sid, _, workspace, body = project
    record, _ = foundation.reserve_foundation(tmp_path, sid, **body)
    calls = []
    runner = fake_run(calls)

    def finish_late(*args, **kwargs):
        result = runner(*args, **kwargs)
        record["deadline_at"] = time.time() - 1
        return result

    monkeypatch.setattr(foundation, "run_map_model", finish_late)
    with pytest.raises(TimeoutError):
        foundation.generate_foundation(tmp_path, sid, record)
    saved = foundation.read_foundation(tmp_path, sid, body["request_id"])
    assert saved["state"] == "failed"
    assert saved["provenance"]["call_id"] == "native-runtime-call"
    assert not (workspace / record["path"]).exists()


def test_permissions_request_validation_and_no_arbitrary_file_write(project, tmp_path, monkeypatch):
    sid, life, _, body = project
    monkeypatch.setattr(foundation, "run_map_model", lambda *a, **kw: pytest.fail("Invalid request must not generate"))
    client = TestClient(create_app(global_root=tmp_path, auth_token="test-only"))
    url = f"/api/projects/{sid}/reader-foundation"
    headers = {"Authorization": "Bearer test-only"}
    assert client.post(url, json=body).status_code == 401
    assert client.post("/api/projects/s-missing/reader-foundation", json=body, headers=headers).status_code == 404
    for invalid in (
        {**body, "request_id": "../file"}, {**body, "question": "   "},
        {**body, "path": "arbitrary.md"}, {**body, "source_task_id": "foreign-task"},
    ):
        assert client.post(url, json=invalid, headers=headers).status_code == 422
    assert client.put(f"/api/projects/{sid}/artifact", json={"path": "file.md", "content": "No"}, headers=headers).status_code == 405
    assert not list((life / foundation.MANIFEST_DIRECTORY).glob("*.json"))


def test_failed_worker_dispatch_cannot_leave_a_live_owner_reservation(project, tmp_path, monkeypatch):
    from fastapi import HTTPException

    from argus_skill.webapi.routes import reader_foundation as routes

    sid, life, _, body = project

    def cannot_start(*args, **kwargs):
        raise HTTPException(503, "worker did not start")

    monkeypatch.setattr(routes, "model_stream_response", cannot_start)
    monkeypatch.setattr(foundation, "run_map_model", lambda *a, **kw: pytest.fail("No worker started"))
    client = TestClient(create_app(global_root=tmp_path))
    response = client.post(f"/api/projects/{sid}/reader-foundation?stream=true", json=body)
    assert response.status_code == 503
    assert not list((life / foundation.MANIFEST_DIRECTORY).glob("*.json"))


def completed_reading(client, sid, body):
    return client.post(f"/api/projects/{sid}/reader-foundation", json=body).json()


@pytest.mark.parametrize("stream", [False, True])
def test_clarification_is_one_bound_artifact_without_manager_or_research_writes(project, tmp_path, monkeypatch, stream):
    from argus_skill.webapi.reader_clarification import QUESTION_MARKER, SOURCES_MARKER

    sid, life, workspace, root_body = project
    calls = []
    monkeypatch.setattr(foundation, "run_map_model", fake_run(calls))
    client = TestClient(create_app(global_root=tmp_path))
    assert foundation.FOUNDATION_VERSION == 3
    with monkeypatch.context() as legacy:
        legacy.setattr(foundation, "FOUNDATION_VERSION", 2)
        root = completed_reading(client, sid, root_body)
    assert root["reader_foundation"]["version"] == 2
    root_manifest = life / foundation.MANIFEST_DIRECTORY / (root_body["request_id"] + ".json")
    manifest_before = root_manifest.read_bytes()
    root_path = workspace / root["path"]
    original = root_path.read_bytes()
    backlog_before = (life / "backlog.jsonl").read_bytes()
    for target in (
        "argus_skill.webapi.manager_bridge.manager_message",
        "argus_skill.apps._inbox.queue_inbox_message",
        "argus_skill.core.transcript.append_turn",
    ):
        monkeypatch.setattr(target, lambda *a, **k: pytest.fail("Reading entered the research message pipeline"))
    request = {"request_id": str(uuid4()), "question": "Why must the two bounds be for the same objective?", "locale": "en-US"}
    url = f"/api/projects/{sid}/reader-foundation/{root_body['request_id']}/question"
    response = client.post(url, params={"stream": stream}, json=request)
    assert response.status_code == 200
    if stream:
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert [row["type"] for row in frames] == ["heartbeat", "progress", "done"]
        child = frames[-1]["result"]
    else:
        child = response.json()
    meta = child["reader_foundation"]
    assert (meta["kind"], meta["parent_id"], meta["root_id"]) == ("clarification", root_body["request_id"], root_body["request_id"])
    assert root["reader_foundation"]["kind"] == "foundation"
    assert root["reader_foundation"]["parent_id"] is None
    assert root["reader_foundation"]["root_id"] == root_body["request_id"]
    assert meta["question"] == request["question"] and meta["state"] == "complete"
    assert meta["version"] == 3
    assert meta["provenance"]["run_label"] == "reader-clarification"
    assert meta["provenance"]["call_id"] == "native-runtime-call"
    assert meta["sources"] == [{"id": root_body["request_id"], "path": root["path"], "title": "Feasible bounds"}]
    assert "sources" not in root["reader_foundation"]
    prompt, _, options = calls[-1]
    snapshot = json.loads(prompt.split(SOURCES_MARKER, 1)[1].split(QUESTION_MARKER, 1)[0])
    assert len(snapshot["sources"]) == 1
    assert snapshot["sources"][0]["markdown"].encode() == original
    assert snapshot["sources"][0]["version"] == 2
    assert json.loads(prompt.split(QUESTION_MARKER, 1)[1]) == request["question"]
    assert options["run_label"] == "reader-clarification" and "mission_id" not in options
    saved = foundation.read_foundation(tmp_path, sid, request["request_id"])
    assert saved["source_snapshot"] == snapshot
    assert request["question"] in saved["markdown"] and "original documents are unchanged" in saved["markdown"]
    assert f"[Feasible bounds]({root['path']})" in saved["markdown"]
    assert client.post(url, json=request).json() == child and len(calls) == 2
    assert client.post(url, json={**request, "question": "Different"}).status_code == 409
    assert client.post(f"/api/projects/{sid}/reader-foundation", json=request).status_code == 409
    listed = client.get(f"/api/projects/{sid}/artifacts?include_reading=true").json()["artifacts"]
    assert [row["reader_foundation"]["id"] for row in listed] == [root_body["request_id"], request["request_id"]]
    assert client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"] == []
    assert root_path.read_bytes() == original and (life / "backlog.jsonl").read_bytes() == backlog_before
    assert root_manifest.read_bytes() == manifest_before
    assert all(not (life / name).exists() for name in ("transcript.jsonl", "inbox.jsonl", "events.jsonl"))


def test_deeper_clarification_uses_only_root_and_selected_parent_and_snapshot_survives_source_change(project, tmp_path, monkeypatch):
    sid, life, workspace, root_body = project
    calls = []
    monkeypatch.setattr(foundation, "run_map_model", fake_run(calls))
    client = TestClient(create_app(global_root=tmp_path))
    root = completed_reading(client, sid, root_body)
    first_body = {"request_id": str(uuid4()), "question": "First doubt", "locale": "en-US"}
    first_url = f"/api/projects/{sid}/reader-foundation/{root_body['request_id']}/question"
    first = client.post(first_url, json=first_body).json()
    second_body = {"request_id": str(uuid4()), "question": "Second doubt", "locale": "en-US"}
    second_url = f"/api/projects/{sid}/reader-foundation/{first_body['request_id']}/question"
    second = client.post(second_url, json=second_body).json()
    request_id = str(uuid4())
    reserved, created = foundation.reserve_foundation(tmp_path, sid, request_id=request_id,
        question="Third doubt", locale="en-US", parent_id=second_body["request_id"])
    assert created and reserved["root_id"] == root_body["request_id"]
    sources = reserved["source_snapshot"]["sources"]
    assert [row["id"] for row in sources] == [root_body["request_id"], second_body["request_id"]]
    assert first_body["request_id"] not in [row["id"] for row in sources]
    (workspace / second["path"]).write_text("Changed after the request was reserved")
    (workspace / root["path"]).unlink()
    result = foundation.generate_foundation(tmp_path, sid, reserved)
    assert result["reader_foundation"]["state"] == "complete"
    assert [source["id"] for source in result["reader_foundation"]["sources"]] == [root_body["request_id"], second_body["request_id"]]
    assert "Changed after the request was reserved" not in calls[-1][0]
    sent = json.loads(calls[-1][0].split("Saved reading sources (JSON):\n")[1].split("\nReader's actual follow-up question")[0])
    assert sent == reserved["source_snapshot"]
    assert foundation.reserve_foundation(tmp_path, sid, request_id=request_id, question="Third doubt",
        locale="en-US", parent_id=second_body["request_id"])[1] is False
    assert client.post(first_url, json=second_body).status_code == 409
    assert len(calls) == 4
    assert first["reader_foundation"]["root_id"] == second["reader_foundation"]["root_id"]


@pytest.mark.parametrize("case", ["missing", "foreign", "generating", "failed", "unreadable", "stale_manifest_text", "empty", "missing_metadata", "missing_root", "invalid_parent_snapshot"])
@pytest.mark.parametrize("stream", [False, True])
def test_clarification_rejects_unavailable_parent_or_root_without_model_or_reservation(project, tmp_path, monkeypatch, case, stream):
    sid, life, workspace, root_body = project
    calls = []
    monkeypatch.setattr(foundation, "run_map_model", fake_run(calls))
    client = TestClient(create_app(global_root=tmp_path))
    root = completed_reading(client, sid, root_body)
    parent_id = root_body["request_id"]
    if case == "missing":
        parent_id = str(uuid4())
    elif case == "foreign":
        other = "s-other"
        write_session_meta(tmp_path, SessionMeta(id=other, workdir=str(workspace)))
        foreign = completed_reading(client, other, {**root_body, "request_id": str(uuid4()), "source_task_id": None})
        parent_id = foreign["reader_foundation"]["id"]
    elif case in {"generating", "failed"}:
        record = foundation.read_foundation(tmp_path, sid, parent_id)
        record["state"] = case
        foundation._save_record(life, record)
    elif case in {"unreadable", "stale_manifest_text"}:
        if case == "stale_manifest_text":
            stale = foundation.read_foundation(tmp_path, sid, parent_id)
            foundation._save_record(life, stale)
        (workspace / root["path"]).unlink()
    elif case == "empty":
        (workspace / root["path"]).write_text(" \n")
    elif case == "missing_metadata":
        invalid = foundation.read_foundation(tmp_path, sid, parent_id)
        invalid.pop("version")
        foundation._save_record(life, invalid)
    elif case in {"missing_root", "invalid_parent_snapshot"}:
        child_body = {"request_id": str(uuid4()), "question": "A doubt", "locale": "en-US"}
        child = client.post(f"/api/projects/{sid}/reader-foundation/{parent_id}/question", json=child_body).json()
        parent_id = child["reader_foundation"]["id"]
        if case == "missing_root":
            (life / foundation.MANIFEST_DIRECTORY / (root_body["request_id"] + ".json")).unlink()
        else:
            invalid = foundation.read_foundation(tmp_path, sid, parent_id)
            invalid["source_snapshot"]["sources"] = []
            foundation._save_record(life, invalid)
    monkeypatch.setattr(foundation, "run_map_model", lambda *a, **k: pytest.fail("Invalid source reached a model"))
    request = {"request_id": str(uuid4()), "question": "Explain this", "locale": "en-US"}
    response = client.post(f"/api/projects/{sid}/reader-foundation/{parent_id}/question", params={"stream": stream}, json=request)
    expected = {"code": "reader_source_unavailable", "message": "The selected reading source is unavailable or incomplete."}
    if stream:
        assert response.status_code == 200
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert frames[-1] == {"type": "error", "error": expected, "status": 422}
        assert not any(row["type"] == "done" for row in frames)
    else:
        assert response.status_code == 422 and response.json()["detail"] == expected
    assert not (life / foundation.MANIFEST_DIRECTORY / (request["request_id"] + ".json")).exists()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("legacy_version", [1, 2])
def test_existing_clarification_replays_terminal_after_source_loss_without_readmission(project, tmp_path, monkeypatch, stream, failed, legacy_version):
    sid, life, workspace, root_body = project
    calls = []
    current_version = foundation.FOUNDATION_VERSION
    assert current_version == 3
    monkeypatch.setattr(foundation, "FOUNDATION_VERSION", legacy_version)
    monkeypatch.setattr(foundation, "run_map_model", fake_run(calls))
    client = TestClient(create_app(global_root=tmp_path))
    root = completed_reading(client, sid, root_body)
    request = {"request_id": str(uuid4()), "question": "Explain the actual doubt", "locale": "en-US"}
    url = f"/api/projects/{sid}/reader-foundation/{root_body['request_id']}/question"
    monkeypatch.setattr(foundation, "run_map_model", fake_run(calls, fail=failed))
    initial = client.post(url, params={"stream": stream}, json=request)
    if failed:
        if stream:
            frames = [json.loads(line[6:]) for line in initial.text.splitlines() if line.startswith("data: ")]
            assert frames[-1] == {"type": "error", "error": "question foundation request or content is invalid", "status": 422}
        else:
            assert initial.status_code == 422 and initial.json()["detail"] == "question foundation request or content is invalid"
    else:
        assert initial.status_code == 200
    assert (life / foundation.MANIFEST_DIRECTORY / (request["request_id"] + ".json")).is_file()
    retained = foundation.foundation_artifact(foundation.read_foundation(tmp_path, sid, request["request_id"]))
    assert retained["reader_foundation"]["version"] == legacy_version
    manifest = life / foundation.MANIFEST_DIRECTORY / (request["request_id"] + ".json")
    before = manifest.read_bytes()
    monkeypatch.setattr(foundation, "FOUNDATION_VERSION", current_version)
    (workspace / root["path"]).unlink()
    monkeypatch.setattr(foundation, "run_map_model", lambda *a, **k: pytest.fail("Terminal clarification regenerated"))
    monkeypatch.setattr(foundation, "generate_foundation", lambda *a, **k: pytest.fail("Terminal clarification was readmitted"))
    monkeypatch.setattr("argus_skill.webapi.reader_clarification.clarification_sources", lambda *a, **k: pytest.fail("Replay reread its parent"))
    replay = client.post(url, params={"stream": stream}, json=request)
    assert replay.status_code == 200
    if stream:
        frames = [json.loads(line[6:]) for line in replay.text.splitlines() if line.startswith("data: ")]
        assert frames[-1] == {"type": "done", "result": retained}
        assert not any(row["type"] == "error" for row in frames)
    else:
        assert replay.json() == retained
    assert retained["reader_foundation"]["state"] == ("failed" if failed else "complete")
    assert manifest.read_bytes() == before
    assert len(calls) == 2


def test_clarification_failed_and_dead_owner_reposts_do_not_regenerate(project, tmp_path, monkeypatch):
    sid, life, workspace, root_body = project
    calls = []
    monkeypatch.setattr(foundation, "run_map_model", fake_run(calls))
    client = TestClient(create_app(global_root=tmp_path))
    completed_reading(client, sid, root_body)
    request = {"request_id": str(uuid4()), "question": "Actual clarification", "locale": "en-US"}
    url = f"/api/projects/{sid}/reader-foundation/{root_body['request_id']}/question"
    monkeypatch.setattr(foundation, "run_map_model", fake_run(calls, fail=True))
    assert client.post(url, json=request).status_code == 422
    failed = client.post(url, json=request).json()
    assert failed["reader_foundation"]["state"] == "failed"
    assert failed["reader_foundation"]["provenance"]["call_id"] == "native-runtime-call"
    assert len(calls) == 2
    record, _ = foundation.reserve_foundation(tmp_path, sid, request_id=str(uuid4()), question="Interrupted question",
        locale="en-US", parent_id=root_body["request_id"])
    record["owner"] = {"pid": 0}
    foundation._save_record(life, record)
    monkeypatch.setattr(foundation, "run_map_model", lambda *a, **k: pytest.fail("Interrupted request regenerated"))
    dead = client.post(url, json={"request_id": record["id"], "question": record["question"], "locale": record["locale"]}).json()
    assert dead["reader_foundation"]["state"] == "failed"
    assert dead["reader_foundation"]["error"] == "generation_owner_terminated"


def test_clarification_request_contract_rejects_untrusted_fields_and_shares_one_worker(project, tmp_path, monkeypatch):
    sid, _, _, root_body = project
    calls = []
    monkeypatch.setattr(foundation, "run_map_model", fake_run(calls))
    client = TestClient(create_app(global_root=tmp_path, auth_token="test-only"))
    headers = {"Authorization": "Bearer test-only"}
    root = client.post(f"/api/projects/{sid}/reader-foundation", json=root_body, headers=headers).json()
    request = {"request_id": str(uuid4()), "question": "Explain this step", "locale": "en-US"}
    url = f"/api/projects/{sid}/reader-foundation/{root_body['request_id']}/question"
    assert client.post(url, json=request).status_code == 401
    for field, value in (("source_task_id", "task-one"), ("source_snapshot", {}), ("parent_id", str(uuid4()))):
        assert client.post(url, json={**request, field: value}, headers=headers).status_code == 422
    started, release = threading.Event(), threading.Event()
    runner = fake_run(calls)

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return runner(*args, **kwargs)

    monkeypatch.setattr(foundation, "run_map_model", blocked)
    with ThreadPoolExecutor() as executor:
        first = executor.submit(client.post, url, json=request, headers=headers)
        try:
            assert started.wait(5)
            duplicate = client.post(url, json=request, headers=headers).json()
            assert duplicate["reader_foundation"]["state"] == "generating"
        finally:
            release.set()
        assert first.result().json()["reader_foundation"]["state"] == "complete"
    assert len(calls) == 2 and root["reader_foundation"]["id"] == root_body["request_id"]
