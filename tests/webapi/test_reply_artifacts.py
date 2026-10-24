from pathlib import Path

from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import append_turn
from argus.webapi.artifacts import get_project_artifact, list_project_artifacts
from argus.webapi.server import create_app


def test_saved_reply_exposes_existing_markdown_without_certifying_delivery(tmp_path: Path) -> None:
    life = tmp_path / "projects" / "s-reply"
    workspace = tmp_path / "workspace"
    life.mkdir(parents=True)
    workspace.mkdir()
    (workspace / "RESEARCH_NOTES.md").write_text("# Existing notes\n", encoding="utf-8")
    (workspace / "unmentioned.md").write_text("not linked", encoding="utf-8")
    write_session_meta(tmp_path, SessionMeta(id="s-reply", cwd=str(life), workdir=str(workspace)))
    append_turn(life, role="argus", text="[Notes](RESEARCH_NOTES.md)")

    rows = list_project_artifacts("s-reply", global_root=tmp_path)
    assert rows is not None
    assert [(row["path"], row["source"]) for row in rows] == [("RESEARCH_NOTES.md", "reply")]
    assert get_project_artifact("s-reply", "unmentioned.md", global_root=tmp_path) is None
    with TestClient(create_app(global_root=tmp_path, auth_token="test-only")) as client:
        url = "/api/projects/s-reply/artifact?path=RESEARCH_NOTES.md"
        assert client.get(url).status_code == 401
        response = client.get(url, headers={"Authorization": "Bearer test-only"})
        assert response.status_code == 200
        assert response.json()["preview"] == "# Existing notes\n"
