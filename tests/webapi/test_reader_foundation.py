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
