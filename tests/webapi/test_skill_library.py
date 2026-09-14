from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.webapi import skill_library as library
from argus_skill.webapi.server import create_app

HEADERS = {"Authorization": "Bearer test-token"}


def skill(path: Path, name: str, content: str = "Instructions in full.") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: |\n  When this applies.\n  What to do.\n---\n\n{content}\n")
    return path


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for name in ("ARGUS_SKILL_SKILLS_DIR", "ARGUS_SKILL_PROJECT_SKILLS_DIR"):
        monkeypatch.delenv(name, raising=False)
    bundled = tmp_path / "package" / "builtin_skills"
    vertical = tmp_path / "package" / "verticals" / "software" / "skills"
    monkeypatch.setattr(library, "builtin_skill_source_path", lambda: bundled)
    monkeypatch.setattr(library, "vertical_skill_source_path", lambda name: vertical)
    monkeypatch.setattr(library, "VERTICALS", ["software"])
    monkeypatch.setattr(library, "resolve_skill_scope", lambda workdir: "software")
    skill(bundled / "engineer/common.md", "Common")
    skill(vertical / "engineer/software.md", "Software")
    home = tmp_path / "state"
    for sid in ("one", "two"):
        workdir = tmp_path / sid
        workdir.mkdir()
        write_session_meta(home, SessionMeta(id=sid, workdir=str(workdir)))
    client = TestClient(create_app(global_root=home, auth_token="test-token"))
    return home, client, bundled


def document(client, *, library_id="project:learned", path="engineer/learned.md", sid="one"):
    return client.get("/api/skill-library/document", headers=HEADERS,
                      params={"library": library_id, "path": path, **({"sid": sid} if sid else {})})


def test_catalog_and_documents_require_auth_and_no_project_is_needed(workspace):
    home, client, _ = workspace
    assert client.get("/api/skill-library").status_code == 401
    assert client.get("/api/skill-library/document", params={"library": "global:bundled", "path": "engineer/common.md"}).status_code == 401
    response = client.get("/api/skill-library", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["scopes"] == ["global", "vertical", "project"]
    assert [(row["scope"], row["name"]) for row in data["items"]] == [("global", "Common"), ("vertical", "Software")]
    assert all(row["is_default"] and row["updated_at"] is None for row in data["items"])
    assert not (home / "skills").exists()
    assert not (home / "projects/one/skills").exists()
    assert document(client, library_id="global:bundled", path="engineer/common.md", sid=None).status_code == 200
    assert client.get("/api/skill-library", params={"sid": "missing"}, headers=HEADERS).status_code == 404


def test_all_classes_recent_changes_dedup_and_full_content(workspace):
    home, client, bundled = workspace
    shared = home / "skills"
    common = skill(shared / "engineer/common.md", "Common")
    skill(shared / "_shared_verticals/software/engineer/common.md", "Common")
    skill(shared / "_shared_verticals/software/engineer/software.md", "Software")
    changed = skill(shared / "_shared_verticals/software/engineer/new.md", "Vertical learning")
    project = skill(home / "projects/one/skills/engineer/learned.md", "Project learning", "Complete content. " * 150)
    assert common.read_bytes() == (bundled / "engineer/common.md").read_bytes()
    os.utime(changed, (100, 100))
    os.utime(project, (200, 200))
    data = client.get("/api/skill-library", params={"sid": "one"}, headers=HEADERS).json()
    assert [row["scope"] for row in data["items"]] == ["global", "vertical", "vertical", "project"]
    common_row = next(row for row in data["items"] if row["name"] == "Common")
    assert common_row["library"] == "global:shared"
    assert common_row["is_default"] and common_row["updated_at"] is None
    recent = [row for row in data["items"] if not row["is_default"]]
    assert {row["name"]: row["updated_at"] for row in recent} == {"Vertical learning": 100, "Project learning": 200}
    result = document(client).json()
    assert result["description"] == "When this applies.\nWhat to do."
    assert len(result["content"]) > 2000
    assert result["content"].count("Complete content.") == 150
    assert result["markdown"].startswith("---\n")
    # Updating an installed global default must appear in recent updates too.
    skill(common, "Global learning")
    data = client.get("/api/skill-library", headers=HEADERS).json()
    assert any(row["scope"] == "global" and not row["is_default"] for row in data["items"])


def test_project_binding_native_skills_and_hidden_files(workspace):
    home, client, _ = workspace
    project = home / "projects/one/skills"
    skill(project / "engineer/learned.md", "One")
    skill(home / "projects/two/skills/engineer/private.md", "Two only")
    skill(home.parent / "one/.agents/skills/author/SKILL.md", "Native")
    skill(home.parent / "one/.agents/skills/author/reference.md", "Not a Skill")
    for relative in ("_archive/old.md", "_history/old.md", "_uncertified/unsafe.md", ".hidden.md", "INDEX.md"):
        skill(project / relative, "Hidden")
        assert document(client, path=relative).status_code == 404
    data = client.get("/api/skill-library", params={"sid": "one"}, headers=HEADERS).json()
    assert {row["name"] for row in data["items"] if row["scope"] == "project"} == {"One", "Native"}
    assert document(client, path="engineer/private.md").status_code == 404
    assert document(client, path="engineer/private.md", sid="two").status_code == 200
    assert document(client, library_id="project:native", path="author/SKILL.md").status_code == 200
    assert document(client, sid=None).status_code == 404


@pytest.mark.parametrize("path", ["../secret.md", "/etc/secret.md", "engineer/../../secret.md", "engineer\\secret.md", "bad\x00.md"])
def test_document_rejects_unconfined_paths(workspace, path):
    _, client, _ = workspace
    assert document(client, path=path).status_code == 404


def test_file_and_library_root_symlinks_cannot_escape(workspace):
    home, client, _ = workspace
    outside = home.parent / "private"
    target = skill(outside / "secret.md", "Secret")
    root = home / "projects/one/skills"
    root.mkdir(parents=True)
    (root / "link.md").symlink_to(target)
    (root / "loop.md").symlink_to(root / "loop.md")
    if hasattr(os, "mkfifo"):
        os.mkfifo(root / "pipe.md")
    assert document(client, path="link.md").status_code == 404
    assert document(client, path="loop.md").status_code == 404
    (home / "projects/two/skills").symlink_to(outside, target_is_directory=True)
    (home / "skills").symlink_to(outside, target_is_directory=True)
    native = home.parent / "two/.agents/skills"
    native.parent.mkdir()
    native.symlink_to(outside, target_is_directory=True)
    for sid in ("one", "two"):
        data = client.get("/api/skill-library", params={"sid": sid}, headers=HEADERS).json()
        assert not any(row["name"] == "Secret" for row in data["items"])
    assert document(client, path="secret.md", sid="two").status_code == 404
    assert document(client, library_id="global:shared", path="secret.md").status_code == 404


def test_vertical_root_symlink_cannot_escape(workspace):
    home, client, _ = workspace
    outside = home.parent / "private"
    skill(outside / "secret.md", "Secret")
    vertical = home / "skills/_shared_verticals/software"
    vertical.parent.mkdir(parents=True)
    vertical.symlink_to(outside, target_is_directory=True)
    assert document(client, library_id="vertical:software:shared", path="secret.md").status_code == 404
    assert not any(row["name"] == "Secret" for row in client.get("/api/skill-library", headers=HEADERS).json()["items"])


def test_browsing_is_read_only_and_oversized_documents_are_explicit(workspace):
    home, client, _ = workspace
    path = skill(home / "projects/one/skills/engineer/learned.md", "Large", "x" * library.MAX_DOCUMENT_BYTES)
    # The app's normal request metrics may be written; project data must not be.
    before = {p.relative_to(home): (p.stat().st_mtime_ns, p.read_bytes()) for p in (home / "projects").rglob("*") if p.is_file()}
    assert client.get("/api/skill-library", params={"sid": "one"}, headers=HEADERS).status_code == 200
    assert document(client).status_code == 413
    after = {p.relative_to(home): (p.stat().st_mtime_ns, p.read_bytes()) for p in (home / "projects").rglob("*") if p.is_file()}
    assert before == after
    path.write_bytes(b"\xff")
    assert document(client).status_code == 409
