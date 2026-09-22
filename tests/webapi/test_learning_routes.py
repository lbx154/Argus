import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.life.answer_learning import _database
from argus.webapi.server import create_app


def test_learning_status_is_project_scoped_and_does_not_expose_the_transcript(tmp_path):
    root = tmp_path / "home"
    for sid in ["one", "two"]:
        write_session_meta(root, SessionMeta(id=sid, workdir=str(tmp_path)))
    with _database(root) as db:
        db.execute("INSERT INTO jobs (id,sid,payload,status,created,updated) VALUES (?,?,?,'failed',1,2)",
                   ("failed-turn", "one", json.dumps({"reply": "private transcript"})))
    client = TestClient(create_app(global_root=root, auth_token="token"))
    auth = {"Authorization": "Bearer token"}
    assert client.get("/api/projects/one/learning").status_code == 401
    assert client.get("/api/projects/missing/learning", headers=auth).status_code == 404
    status = client.get("/api/projects/one/learning", headers=auth)
    assert status.json()["jobs"][0]["status"] == "failed"
    assert "private transcript" not in status.text
    assert client.get("/api/projects/two/learning", headers=auth).json()["jobs"] == []
    assert client.post("/api/projects/two/learning/failed-turn/retry", headers=auth).status_code == 409


def test_startup_and_retry_reconstruct_learning_through_the_delivery_owner(tmp_path, monkeypatch):
    from argus.life import answer_learning, reflection
    from argus.manager import front_door
    from argus.webapi import manager_state
    from argus.webapi.routes import learning

    root = (tmp_path / "home").resolve()
    sid = "learning-restart"
    write_session_meta(root, SessionMeta(id=sid, workdir=str(tmp_path)))
    with _database(root) as db:
        db.execute("INSERT INTO jobs (id,sid,payload,status,created,updated) VALUES (?,?,?,'running',1,2)",
                   ("interrupted", sid, json.dumps({"operator_text": "request", "reply": "delivered"})))
    backend = object()
    reconstructed, reflected, threads = [], [], []

    def runner(state, memory):
        reconstructed.append((state["session_id"], state["global_root"], memory.project_root))
        return SimpleNamespace(_backend=backend)

    def reflect(**kwargs):
        reflected.append(kwargs["runner_backend"])
        return {"failure": "offline"} if len(reflected) == 1 else {}

    original_resume = answer_learning.resume_learning

    def resume(*args, **kwargs):
        thread = original_resume(*args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(front_door, "_ensure_manager_runner", runner)
    monkeypatch.setattr(reflection, "reflect_after_answer", reflect)
    monkeypatch.setattr(answer_learning, "resume_learning", resume)
    monkeypatch.setattr(learning, "resume_learning", resume)
    try:
        with TestClient(create_app(global_root=root)) as client:
            threads[-1].join(timeout=5)
            assert not threads[-1].is_alive()
            assert client.get(f"/api/projects/{sid}/learning").json()["jobs"][0]["status"] == "failed"
            assert client.post(f"/api/projects/{sid}/learning/interrupted/retry").status_code == 200
            threads[-1].join(timeout=5)
            assert not threads[-1].is_alive()
            job = client.get(f"/api/projects/{sid}/learning").json()["jobs"][0]
            assert job["status"] == "unchanged" and job["attempts"] == 2
        assert reflected == [backend, backend]
        assert reconstructed == [(sid, str(root), root / "projects" / sid)] * 2
        assert root not in answer_learning._BACKEND_FACTORIES
    finally:
        for thread in threads:
            thread.join(timeout=5)
        manager_state._STATES.pop(sid, None)
