import json

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
