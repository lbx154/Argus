from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
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


# --- knowledge tiers: /api/wiki and /api/wiki/page --------------------------


def _decide_vertical(workspace: Path, vertical: str) -> None:
    state = workspace / ".argus" / "PIPELINE_STATE.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(f'{{"vertical": "{vertical}"}}', encoding="utf-8")


def _shared(home: Path, *parts: str, index: str | None = None) -> Path:
    root = home.joinpath("wiki", *parts)
    (root / "pages").mkdir(parents=True)
    if index is not None:
        (root / "INDEX.md").write_text(index, encoding="utf-8")
    return root


def test_knowledge_without_project_lists_only_shared_tiers(tmp_path):
    client, workspace = _client(tmp_path)
    home = tmp_path / "state"
    _wiki(workspace)
    _shared(home, "_global")
    _shared(home, "_shared_verticals", "software")
    _shared(home, "_shared_verticals", "research")

    assert client.get("/api/wiki").status_code == 401
    body = client.get("/api/wiki", headers=HEADERS).json()
    assert [(lib["scope"], lib["vertical"]) for lib in body["libraries"]] == [
        ("vertical", "research"),
        ("vertical", "software"),
        ("global", ""),
    ]
    assert body["verticals"] == ["research", "software"]
    assert body["active_vertical"] == ""
    assert body["items"] == []
    assert client.get("/api/wiki", params={"sid": "missing"}, headers=HEADERS).status_code == 404


def test_knowledge_page_strips_front_matter_and_routes_by_scope(tmp_path):
    client, workspace = _client(tmp_path)
    home = tmp_path / "state"
    project = _wiki(workspace)
    _decide_vertical(workspace, "research")
    _write(project / "pages" / "queue.md", _page("Queue", "How the queue works", "# Queue\n\nProject body."), 2_000)
    research = _shared(home, "_shared_verticals", "research")
    _write(research / "pages" / "eval" / "protocol.md", _page("Protocol", "Shared protocol", "Vertical body."), 3_000)
    shared_global = _shared(home, "_global")
    _write(shared_global / "pages" / "hosts.md", _page("Hosts", "Host facts", "Global body."), 1_000)

    page = client.get(
        "/api/wiki/page", params={"scope": "project", "sid": "demo", "path": "pages/queue.md"}, headers=HEADERS
    )
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["scope"] == "project"
    assert body["vertical"] == "research"
    assert body["path"] == "pages/queue.md"
    assert body["title"] == "Queue"
    assert body["description"] == "How the queue works"
    assert body["content"] == "# Queue\n\nProject body.\n"
    assert body["markdown"].startswith("---\ntitle: Queue")
    assert body["truncated"] is False
    assert body["updated_at"] == pytest.approx(2_000)

    vertical = client.get(
        "/api/wiki/page",
        params={"scope": "vertical", "vertical": "research", "path": "pages/eval/protocol.md"},
        headers=HEADERS,
    ).json()
    assert vertical["scope"] == "vertical"
    assert vertical["vertical"] == "research"
    assert vertical["content"] == "Vertical body.\n"

    global_page = client.get(
        "/api/wiki/page", params={"scope": "global", "path": "pages/hosts.md"}, headers=HEADERS
    ).json()
    assert global_page["scope"] == "global"
    assert global_page["vertical"] == ""
    assert global_page["content"] == "Global body.\n"

    # A page without front matter is served as-is.
    _write(shared_global / "pages" / "legacy.md", "# Legacy\n\nOld page.\n", 500)
    legacy = client.get(
        "/api/wiki/page", params={"scope": "global", "path": "pages/legacy.md"}, headers=HEADERS
    ).json()
    assert legacy["title"] == "Legacy"
    assert legacy["content"] == legacy["markdown"] == "# Legacy\n\nOld page.\n"


def test_knowledge_page_errors_follow_the_project_rules(tmp_path):
    client, workspace = _client(tmp_path)
    home = tmp_path / "state"
    shared_global = _shared(home, "_global")
    _write(shared_global / "pages" / "hosts.md", _page("Hosts", "Host facts", "Body."), 1_000)
    (home / "secret.md").write_text("# Secret\n", encoding="utf-8")
    (shared_global / "pages" / "escape.md").symlink_to(home / "secret.md")

    def get(**params):
        return client.get("/api/wiki/page", params=params, headers=HEADERS)

    assert get(scope="global", path="pages/nope.md").status_code == 404
    assert get(scope="vertical", vertical="research", path="pages/hosts.md").status_code == 404
    assert get(scope="vertical", path="pages/hosts.md").status_code == 404
    assert get(scope="project", path="pages/hosts.md").status_code == 404
    assert get(scope="project", sid="demo", path="pages/hosts.md").status_code == 404
    assert get(scope="project", sid="missing", path="pages/hosts.md").status_code == 404
    assert get(scope="library", path="pages/hosts.md").status_code == 404
    for path in ("pages/../INDEX.md", "../secret.md", "INDEX.md", "pages/escape.md", "pages/notes.txt"):
        response = get(scope="global", path=path)
        assert response.status_code == 409, (path, response.text)
        assert "Secret" not in response.text
    assert client.get("/api/wiki/page", params={"scope": "global", "path": "pages/hosts.md"}).status_code == 401


# --- page metadata, reuse counts, principles and the knowledge feed ----------


def _lesson(title: str, description: str, body: str, **front: str) -> str:
    extra = "".join(f"{key}: {value}\n" for key, value in front.items())
    return f"---\ntitle: {title}\ndescription: {description}\n{extra}---\n\n{body}\n"


def test_knowledge_pages_carry_kind_source_created(tmp_path):
    client, workspace = _client(tmp_path)
    home = tmp_path / "state"
    project = _wiki(workspace)
    _decide_vertical(workspace, "research")
    _write(project / "pages" / "queue.md", _page("Queue", "How the queue works", "Body."), 2_000)
    research = _shared(home, "_shared_verticals", "research")
    lesson = research / "pages" / "lessons" / "20260917-torch-search.md"
    _write(
        lesson,
        _lesson(
            "Search torch docs first", "Read before guessing", "Body.",
            kind="lesson", source="s-fb4716b7/cf2c076f939e", created="2026-09-17",
            audience="vertical", confidence="high",
        ),
        3_000,
    )
    # A kind the library does not know is shown as a plain page.
    _write(research / "pages" / "odd.md", _lesson("Odd", "Unknown kind", "Body.", kind="rumour"), 2_500)
    shared_global = _shared(home, "_global")
    _write(shared_global / "pages" / "hosts.md", _page("Hosts", "Host facts", "Body."), 1_000)

    body = client.get("/api/wiki", params={"sid": "demo"}, headers=HEADERS).json()
    project_lib, vertical_lib, global_lib = body["libraries"]
    (queue,) = project_lib["pages"]
    assert queue["kind"] == "page"
    assert queue["source"] == ""
    assert queue["created"] == ""
    torch, odd = vertical_lib["pages"]
    assert torch["kind"] == "lesson"
    assert torch["source"] == "s-fb4716b7/cf2c076f939e"
    assert torch["created"] == "2026-09-17"
    assert odd["kind"] == "page"
    (hosts,) = global_lib["pages"]
    assert hosts["kind"] == "page"
    assert [(row["path"], row["kind"]) for row in body["items"]] == [
        ("pages/lessons/20260917-torch-search.md", "lesson"),
        ("pages/odd.md", "page"),
        ("pages/queue.md", "page"),
        ("pages/hosts.md", "page"),
    ]
    assert body["items"][0]["source"] == "s-fb4716b7/cf2c076f939e"

    page = client.get(
        "/api/wiki/page",
        params={"scope": "vertical", "vertical": "research", "path": "pages/lessons/20260917-torch-search.md"},
        headers=HEADERS,
    ).json()
    assert (page["kind"], page["source"], page["created"]) == ("lesson", "s-fb4716b7/cf2c076f939e", "2026-09-17")


def test_knowledge_libraries_carry_their_principles(tmp_path):
    client, workspace = _client(tmp_path)
    home = tmp_path / "state"
    _wiki(workspace)
    _decide_vertical(workspace, "research")
    research = _shared(home, "_shared_verticals", "research")
    (research / "principles.md").write_text(
        "---\ntitle: Research principles\ndescription: Compiled from lessons\nkind: principles\n---\n\n"
        "1. Read the docs before guessing — evidence: [a](pages/lessons/a.md), [b](pages/lessons/b.md)\n\n"
        "## History\n\n- 2026-09-17 compiled from 2 lessons\n",
        encoding="utf-8",
    )
    _shared(home, "_shared_verticals", "software")
    _shared(home, "_global")

    body = client.get("/api/wiki", params={"sid": "demo"}, headers=HEADERS).json()
    by_key = {(lib["scope"], lib["vertical"]): lib for lib in body["libraries"]}
    assert by_key[("vertical", "research")]["principles"] == (
        "1. Read the docs before guessing — evidence: [a](pages/lessons/a.md), [b](pages/lessons/b.md)\n\n"
        "## History\n\n- 2026-09-17 compiled from 2 lessons\n"
    )
    assert by_key[("vertical", "software")]["principles"] is None
    assert by_key[("global", "")]["principles"] is None
    assert by_key[("project", "research")]["principles"] is None


def test_project_wiki_rows_carry_kind_source_created(tmp_path):
    client, workspace = _client(tmp_path)
    root = _wiki(workspace)
    _write(
        root / "pages" / "facts" / "gpu.md",
        _lesson("GPU", "Which GPU the host has", "Body.", kind="fact", source="chat/8f3a2b1c", created="2026-09-16"),
        2_000,
    )
    _write(root / "pages" / "legacy.md", "# Legacy heading\n\nOld page.\n", 1_000)

    body = client.get("/api/projects/demo/wiki", headers=HEADERS).json()
    gpu, legacy = body["pages"]
    assert (gpu["kind"], gpu["source"], gpu["created"]) == ("fact", "chat/8f3a2b1c", "2026-09-16")
    assert (legacy["kind"], legacy["source"], legacy["created"]) == ("page", "", "")
    page = client.get("/api/projects/demo/wiki/page", params={"path": "pages/facts/gpu.md"}, headers=HEADERS).json()
    assert (page["kind"], page["source"], page["created"]) == ("fact", "chat/8f3a2b1c", "2026-09-16")
    assert page["content"] == "Body.\n"
