from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi.routes.wiki import INDEX_LIMIT, PAGE_LIMIT
from argus.webapi.server import create_app

HEADERS = {"Authorization": "Bearer token"}


def _page(title: str, description: str, body: str) -> str:
    return f"---\ntitle: {title}\ndescription: {description}\n---\n\n{body}\n"


def _client(tmp_path) -> tuple[TestClient, Path]:
    home, workspace = tmp_path / "state", tmp_path / "workspace"
    workspace.mkdir()
    write_session_meta(home, SessionMeta(id="demo", workdir=str(workspace)))
    return TestClient(create_app(global_root=home, auth_token="token")), workspace


def _wiki(workspace: Path, project: str = "proj") -> Path:
    root = workspace / ".autors" / project / "wiki"
    (root / "pages").mkdir(parents=True)
    (root / "INDEX.md").write_text("# Index\n\n- [Queue](pages/queue.md)\n", encoding="utf-8")
    return root


def _write(path: Path, text: str, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_wiki_requires_auth_and_unknown_project_is_404(tmp_path):
    client, _workspace = _client(tmp_path)
    assert client.get("/api/projects/demo/wiki").status_code == 401
    assert client.get("/api/projects/missing/wiki", headers=HEADERS).status_code == 404
    assert client.get("/api/projects/missing/wiki/page?path=pages/x.md", headers=HEADERS).status_code == 404


def test_wiki_reports_absence_without_creating_anything(tmp_path):
    client, workspace = _client(tmp_path)
    response = client.get("/api/projects/demo/wiki", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"exists": False}
    assert not (workspace / ".autors").exists()
    # A page request against a project without a wiki is a plain 404.
    assert client.get("/api/projects/demo/wiki/page?path=pages/x.md", headers=HEADERS).status_code == 404


def test_wiki_lists_index_and_pages_newest_first(tmp_path):
    client, workspace = _client(tmp_path)
    root = _wiki(workspace)
    _write(root / "pages" / "queue.md", _page("Queue", "How the queue works", "Body."), 1_000)
    _write(root / "pages" / "arch" / "encoder.md", _page("Encoder", "Backbone notes", "Body."), 3_000)
    # Legacy page without front matter falls back to its first H1.
    _write(root / "pages" / "legacy.md", "# Legacy heading\n\nOld page.\n", 2_000)
    # Hidden files under pages/ are not pages.
    _write(root / "pages" / ".draft.md", _page("Draft", "hidden", "x"), 9_000)
    response = client.get("/api/projects/demo/wiki", headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exists"] is True
    assert body["root"] == ".autors/proj/wiki"
    assert body["index_markdown"].startswith("# Index")
    assert [row["path"] for row in body["pages"]] == [
        "pages/arch/encoder.md",
        "pages/legacy.md",
        "pages/queue.md",
    ]
    encoder, legacy, queue = body["pages"]
    assert encoder["title"] == "Encoder"
    assert encoder["description"] == "Backbone notes"
    assert encoder["updated_at"] == pytest.approx(3_000)
    assert legacy["title"] == "Legacy heading"
    assert legacy["description"] == ""
    assert queue["updated_at"] == pytest.approx(1_000)


def test_wiki_picks_the_first_discovered_wiki(tmp_path):
    client, workspace = _client(tmp_path)
    _wiki(workspace, "beta")
    _wiki(workspace, "alpha")
    body = client.get("/api/projects/demo/wiki", headers=HEADERS).json()
    assert body["root"] == ".autors/alpha/wiki"


def test_wiki_page_is_read_with_title_and_timestamp(tmp_path):
    client, workspace = _client(tmp_path)
    root = _wiki(workspace)
    _write(root / "pages" / "arch" / "encoder.md", _page("Encoder", "Backbone notes", "The backbone."), 3_000)
    response = client.get("/api/projects/demo/wiki/page", params={"path": "pages/arch/encoder.md"}, headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["path"] == "pages/arch/encoder.md"
    assert body["title"] == "Encoder"
    assert body["markdown"].endswith("The backbone.\n")
    assert body["truncated"] is False
    assert body["updated_at"] == pytest.approx(3_000)
    assert client.get("/api/projects/demo/wiki/page", params={"path": "pages/nope.md"}, headers=HEADERS).status_code == 404
    # The route only reads; a POST is not offered.
    assert client.post("/api/projects/demo/wiki/page", headers=HEADERS, json={}).status_code == 405


def test_wiki_page_refuses_paths_leaving_the_pages_directory(tmp_path):
    client, workspace = _client(tmp_path)
    root = _wiki(workspace)
    (workspace / "secret.md").write_text("# Secret\n", encoding="utf-8")
    (root / "pages" / "escape.md").symlink_to(workspace / "secret.md")
    for path in (
        "pages/../INDEX.md",
        "../../../secret.md",
        "INDEX.md",
        str(workspace / "secret.md"),
        "pages/escape.md",
        "pages/notes.txt",
    ):
        response = client.get("/api/projects/demo/wiki/page", params={"path": path}, headers=HEADERS)
        assert response.status_code == 409, (path, response.text)
        assert "Secret" not in response.text
    listing = client.get("/api/projects/demo/wiki", headers=HEADERS).json()
    assert [row["path"] for row in listing["pages"]] == []


def test_wiki_truncates_large_index_and_page(tmp_path):
    client, workspace = _client(tmp_path)
    root = _wiki(workspace)
    (root / "INDEX.md").write_text("# Index\n" + "x" * (INDEX_LIMIT + 100), encoding="utf-8")
    _write(root / "pages" / "big.md", _page("Big", "Large page", "y" * (PAGE_LIMIT + 100)), 1_000)
    body = client.get("/api/projects/demo/wiki", headers=HEADERS).json()
    assert len(body["index_markdown"].encode("utf-8")) <= INDEX_LIMIT
    assert body["pages"][0]["title"] == "Big"
    page = client.get("/api/projects/demo/wiki/page", params={"path": "pages/big.md"}, headers=HEADERS).json()
    assert page["truncated"] is True
    assert page["title"] == "Big"
    assert len(page["markdown"].encode("utf-8")) <= PAGE_LIMIT
