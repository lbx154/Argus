"""Wave-1 tests: the read/inspect + backlog-lifecycle endpoints (1:1 with the
Python cockpit's /status /journal /note /doctor /config /identity /transcript
and /done /skip /rm /stop). Real temp project; no daemon needed."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.session import SessionMeta, read_session_meta, write_session_meta
from argus_skill.life.memory import LifeMemory
from argus_skill.manager import front_door
from argus_skill.webapi import artifacts, manager_bridge, server

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


def _make_project(root: Path, sid: str = "s-w1000001") -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(
        json.dumps({"type": "mission.started", "text": "hi", "ts": time.time()}) + "\n",
        encoding="utf-8",
    )
    (life / "backlog.jsonl").write_text(
        json.dumps({"id": "item1", "title": "tune kernel", "objective": "tune the kernel",
                    "status": "pending", "priority": 100, "iterate": True,
                    "max_cost_usd": 30.0, "ts": time.time()}) + "\n",
        encoding="utf-8",
    )
    (life / "journal.jsonl").write_text(
        json.dumps({"id": "j1", "ts": time.time(), "kind": "mission_complete",
                    "title": "did a thing", "summary": "completed a mission", "tags": []}) + "\n",
        encoding="utf-8",
    )
    (life / "transcript.jsonl").write_text(
        json.dumps({"role": "operator", "text": "复现 Task-3", "ts": time.time()}) + "\n"
        + json.dumps({"role": "argus", "text": "on it", "ts": time.time()}) + "\n",
        encoding="utf-8",
    )
    return life


@pytest.fixture()
def ctx(tmp_path: Path):
    life = _make_project(tmp_path)
    return tmp_path, "s-w1000001", life, TestClient(server.create_app(global_root=tmp_path))


def test_create_daemon_persists_launch_cwd(tmp_path: Path) -> None:
    launch = tmp_path / "workspace"
    launch.mkdir()
    created = server.create_daemon("", launch_cwd=str(launch), global_root=tmp_path)
    meta = read_session_meta(tmp_path, created["sid"])
    assert meta is not None
    assert meta.launch_cwd == str(launch.resolve())
    assert meta.origin == "web"


def test_launch_cwd_update_preserves_existing_session_name(tmp_path: Path) -> None:
    created = server.create_daemon(name="Existing name", global_root=tmp_path)
    launch = tmp_path / "new-workspace"
    launch.mkdir()

    assert server.set_project_launch_cwd(
        created["sid"],
        str(launch),
        global_root=tmp_path,
    )

    meta = read_session_meta(tmp_path, created["sid"])
    assert meta is not None
    assert meta.display_name == "Existing name"
    assert meta.launch_cwd == str(launch.resolve())


def test_web_context_defaults_launch_cwd_and_reports_it(
    tmp_path: Path, monkeypatch,
) -> None:
    launch = tmp_path / "web-workspace"
    launch.mkdir()
    monkeypatch.chdir(launch)
    client = TestClient(server.create_app(global_root=tmp_path))

    created = client.post("/api/daemons", json={}).json()
    meta = read_session_meta(tmp_path, created["sid"])
    index = client.get("/api/projects").json()

    assert meta is not None
    assert meta.launch_cwd == str(launch.resolve())
    assert index["local_cwd"] == str(launch.resolve())
    assert created["sid"] in {row["id"] for row in index["projects"]}


def test_set_project_launch_cwd_claims_legacy_session(tmp_path: Path) -> None:
    life = _make_project(tmp_path, sid="s-legacy1")
    assert server.set_project_launch_cwd(
        "s-legacy1", str(tmp_path / "workspace"), global_root=tmp_path,
    )
    meta = read_session_meta(tmp_path, "s-legacy1")
    assert meta is not None
    assert meta.cwd == str(life)
    assert meta.launch_cwd == str((tmp_path / "workspace").resolve())


# ── read/inspect ────────────────────────────────────────────────────────────

def test_status_composite(ctx) -> None:
    _, sid, _, client = ctx
    body = client.get(f"/api/projects/{sid}/status").json()
    assert set(body) >= {
        "identity", "backlog_pending", "pending_questions", "journal",
        "continuous", "inbox_pending", "daemon", "roles", "active_role",
    }
    assert len(body["roles"]) == 4
    assert body["backlog_pending"][0]["objective"] == "tune the kernel"
    assert body["continuous"]["enabled"] is False


def test_journal(ctx) -> None:
    _, sid, _, client = ctx
    j = client.get(f"/api/projects/{sid}/journal?n=5").json()["journal"]
    assert isinstance(j, list)


def test_doctor(ctx) -> None:
    _, sid, _, client = ctx
    d = client.get(f"/api/projects/{sid}/doctor").json()
    assert isinstance(d["checks"], list) and len(d["checks"]) >= 1
    assert all(set(c) == {"name", "ok", "detail", "fix"} for c in d["checks"])
    assert "log_tail" in d


def test_config(ctx) -> None:
    _, sid, _, client = ctx
    cfg = client.get(f"/api/projects/{sid}/config").json()
    assert "roles" in cfg and len(cfg["roles"]) == 4


def test_identity(ctx) -> None:
    _, sid, _, client = ctx
    assert isinstance(client.get(f"/api/projects/{sid}/identity").json()["identity"], str)


def test_transcript(ctx) -> None:
    _, sid, _, client = ctx
    turns = client.get(f"/api/projects/{sid}/transcript").json()["turns"]
    assert isinstance(turns, list) and len(turns) == 2


def test_backlog_item_returns_full_objective(ctx) -> None:
    _, sid, _, client = ctx
    response = client.get(f"/api/projects/{sid}/backlog/item1")
    assert response.status_code == 200
    item = response.json()["item"]
    assert item["id"] == "item1"
    assert item["objective"] == "tune the kernel"
    assert "iteration_cycles_done" in item
    assert client.get(f"/api/projects/{sid}/backlog/nope").status_code == 404


def test_projects_enriched_with_label_and_uptime(ctx) -> None:
    root, sid, _, client = ctx
    p = next(p for p in client.get("/api/projects").json()["projects"] if p["id"] == sid)
    assert "label" in p and "uptime_seconds" in p


def test_project_picker_uses_campaign_objective_before_greeting(
    ctx, monkeypatch,
) -> None:
    root, sid, _, client = ctx
    manager_bridge._STATES.clear()

    class _Manager:
        def decide_vertical(self, text, **kwargs):
            return SimpleNamespace(execution_task=text)

        def commit_vertical_decision(self, text, decision, **kwargs):
            return SimpleNamespace(execution_task=decision.execution_task)

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda chat_state, mem: SimpleNamespace(manager=_Manager()),
    )
    assert server.set_continuous(
        sid, enabled=True, objective="Write the CO2 paper", global_root=root,
    ) is True
    p = next(p for p in client.get("/api/projects").json()["projects"] if p["id"] == sid)
    assert p["objective"] == "Write the CO2 paper"
    assert p["label"] == "Write the CO2 paper"


def _seed_result_artifacts(root: Path, sid: str, life: Path) -> Path:
    workspace = root / "workspace"
    (workspace / "paper").mkdir(parents=True)
    (workspace / "paper" / "result.md").write_text("# Certified\nreal result\n", encoding="utf-8")
    (workspace / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (workspace / ".review-note").write_text("hidden evidence\n", encoding="utf-8")
    (workspace / "secret.txt").write_text("not allowlisted", encoding="utf-8")
    outside = root / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (workspace / "paper" / "escaped-link.txt").symlink_to(outside)
    write_session_meta(root, SessionMeta(id=sid, cwd=str(workspace)))
    with (life / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "life.mission.completed",
            "item_id": "result-1",
            "success": True,
            "title": "certified result",
            "ts": time.time(),
            "planner_report": {
                "evidence_files": [
                    {"path": "paper/result.md", "why": "reviewed output"},
                    {"path": "pyproject.toml", "why": "runtime configuration"},
                    {"path": "paper/missing.json", "why": "declared but missing"},
                    {"path": "./.review-note", "why": "dotfile path must survive normalization"},
                    {"path": "../outside.txt", "why": "must be rejected"},
                    {"path": str(outside), "why": "absolute path must be rejected"},
                    {"path": "paper/escaped-link.txt", "why": "symlink escape must be rejected"},
                ],
            },
        }) + "\n")
    return workspace


def test_artifacts_are_latest_result_allowlisted_and_workspace_confined(ctx) -> None:
    root, sid, life, client = ctx
    _seed_result_artifacts(root, sid, life)

    rows = client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"]
    assert [row["path"] for row in rows] == [
        "paper/result.md", "pyproject.toml", "paper/missing.json",
    ]
    assert rows[0]["exists"] is True
    assert rows[1]["exists"] is True
    assert rows[2]["exists"] is False
    assert client.get(f"/api/projects/{sid}/artifacts").headers["cache-control"] == "private, no-store"

    info = client.get(
        f"/api/projects/{sid}/artifact", params={"path": "paper/result.md"},
    )
    assert info.status_code == 200
    assert info.json()["preview"].startswith("# Certified")
    assert info.json()["kind"] == "markdown"
    assert info.headers["cache-control"] == "private, no-store"

    toml = client.get(
        f"/api/projects/{sid}/artifact", params={"path": "pyproject.toml"},
    )
    assert toml.status_code == 200
    assert toml.json()["kind"] == "text"

    raw = client.get(
        f"/api/projects/{sid}/artifact/raw", params={"path": "paper/result.md"},
    )
    assert raw.status_code == 200
    assert raw.text.startswith("# Certified")
    assert raw.headers["content-type"].startswith("text/plain")
    assert raw.headers["x-content-type-options"] == "nosniff"

    download = client.get(
        f"/api/projects/{sid}/artifact/raw",
        params={"path": "paper/result.md", "download": "true"},
    )
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]

    for forbidden in (
        "secret.txt", "../outside.txt", str(root / "outside.txt"), "paper/escaped-link.txt",
    ):
        assert client.get(
            f"/api/projects/{sid}/artifact", params={"path": forbidden},
        ).status_code == 404
    assert client.get(
        f"/api/projects/{sid}/artifact", params={"path": "paper/missing.json"},
    ).status_code == 404
    hidden = client.get(
        f"/api/projects/{sid}/artifact", params={"path": ".review-note"},
    )
    assert hidden.status_code == 404


def test_artifacts_use_session_workspace_instead_of_launch_directory(ctx) -> None:
    root, sid, life, client = ctx
    launch = root / "launch"
    (launch / "paper").mkdir(parents=True)
    (launch / "paper" / "result.md").write_text("wrong project\n", encoding="utf-8")
    (life / "paper").mkdir()
    (life / "paper" / "result.md").write_text("current session\n", encoding="utf-8")
    write_session_meta(
        root,
        SessionMeta(id=sid, cwd=str(life), launch_cwd=str(launch)),
    )
    with (life / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "life.mission.completed",
            "item_id": "isolated-result",
            "success": True,
            "ts": time.time(),
            "planner_report": {
                "evidence_files": [{"path": "paper/result.md", "why": "final"}],
            },
        }) + "\n")

    preview = client.get(
        f"/api/projects/{sid}/artifact", params={"path": "paper/result.md"},
    )

    assert preview.status_code == 200
    assert preview.json()["preview"] == "current session\n"


def test_artifact_allowlist_is_replaced_by_newest_result(ctx) -> None:
    root, sid, life, client = ctx
    _seed_result_artifacts(root, sid, life)
    with (life / "events.jsonl").open("a", encoding="utf-8") as fh:
        for index in range(150):
            fh.write(json.dumps({
                "type": "user.note", "text": f"later note {index}", "ts": time.time(),
            }) + "\n")
    # Unrelated journal traffic must not make the latest reviewed result vanish.
    assert client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"]

    with (life / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "life.mission.completed",
            "item_id": "result-2",
            "success": True,
            "title": "newer result without files",
            "ts": time.time() + 1,
            "planner_report": {},
        }) + "\n")

    assert client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"] == []
    assert client.get(
        f"/api/projects/{sid}/artifact", params={"path": "paper/result.md"},
    ).status_code == 404


def test_manager_live_view_is_available_during_active_work(ctx) -> None:
    root, sid, life, client = ctx
    workspace = root / "workspace-live"
    (workspace / "research").mkdir(parents=True)
    (workspace / "research" / "PROGRESS.md").write_text(
        "# Live progress\n", encoding="utf-8"
    )
    (workspace / ".argus").mkdir()
    (workspace / ".argus" / "live-view.json").write_text(
        json.dumps({
            "version": 1,
            "title": "Current research",
            "reason": "The Manager selected the changing research log.",
            "paths": ["research/PROGRESS.md", ".env", "../outside.txt"],
        }),
        encoding="utf-8",
    )
    write_session_meta(root, SessionMeta(id=sid, cwd=str(workspace)))

    rows = client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"]

    assert [row["path"] for row in rows] == ["research/PROGRESS.md"]
    assert rows[0]["source"] == "manager_live"
    assert rows[0]["group_title"] == "Current research"
    preview = client.get(
        f"/api/projects/{sid}/artifact", params={"path": "research/PROGRESS.md"},
    )
    assert preview.status_code == 200
    assert preview.json()["preview"].startswith("# Live progress")


def test_manager_live_view_uses_life_dir_without_session_metadata(ctx) -> None:
    _, sid, life, client = ctx
    (life / ".argus").mkdir()
    (life / ".argus" / "live-view.json").write_text(
        json.dumps({
            "version": 1,
            "title": "Long-running research",
            "paths": ["FINAL_REPORT.md"],
        }),
        encoding="utf-8",
    )
    (life / "FINAL_REPORT.md").write_text("# Living report\n", encoding="utf-8")

    rows = client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"]

    assert [row["path"] for row in rows] == ["FINAL_REPORT.md"]
    assert rows[0]["source"] == "manager_live"
    preview = client.get(
        f"/api/projects/{sid}/artifact", params={"path": "FINAL_REPORT.md"},
    )
    assert preview.status_code == 200
    assert preview.json()["preview"].startswith("# Living report")


def test_artifacts_prefer_executor_cwd_over_launch_metadata(ctx) -> None:
    root, sid, life, client = ctx
    launch = root / "launch-context"
    launch.mkdir()
    (life / ".argus").mkdir()
    (life / ".argus" / "live-view.json").write_text(
        json.dumps({
            "version": 1,
            "title": "Current output",
            "reason": "Written by the isolated Web executor.",
            "paths": ["result.md"],
        }),
        encoding="utf-8",
    )
    (life / "result.md").write_text("# Real executor output\n", encoding="utf-8")
    write_session_meta(
        root,
        SessionMeta(id=sid, cwd=str(life), launch_cwd=str(launch)),
    )

    rows = client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"]

    assert rows[0]["path"] == "result.md"
    assert rows[0]["exists"] is True
    assert rows[0]["source"] == "manager_live"
    assert rows[0]["group_title"] == "Current output"


def test_manager_live_symlink_cannot_expose_sensitive_workspace_file(ctx) -> None:
    root, sid, life, client = ctx
    (life / ".env").write_text("SECRET=do-not-serve\n", encoding="utf-8")
    live = life / ".argus" / "live"
    live.mkdir(parents=True)
    (live / "current.md").symlink_to(life / ".env")
    (life / ".argus" / "live-view.json").write_text(
        json.dumps({
            "version": 1,
            "title": "Unsafe",
            "reason": "Must be rejected after symlink resolution.",
            "paths": [".argus/live/current.md"],
        }),
        encoding="utf-8",
    )
    write_session_meta(root, SessionMeta(id=sid, cwd=str(life)))

    rows = client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"]

    assert rows == []


def test_sensitive_symlink_alias_is_rejected_even_with_safe_target(ctx) -> None:
    root, sid, life, client = ctx
    (life / "public.md").write_text("not secret\n", encoding="utf-8")
    (life / "credentials.json").symlink_to(life / "public.md")
    with (life / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "life.mission.completed",
            "item_id": "sensitive-alias",
            "success": True,
            "ts": time.time() + 1,
            "planner_report": {
                "evidence_files": [{"path": "credentials.json"}],
            },
        }) + "\n")
    write_session_meta(root, SessionMeta(id=sid, cwd=str(life)))

    rows = client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"]

    assert rows == []


def test_manager_live_cannot_select_common_credential_file(ctx) -> None:
    root, sid, life, client = ctx
    (life / ".npmrc").write_text("//registry/:_authToken=secret\n", encoding="utf-8")
    (life / ".argus").mkdir()
    (life / ".argus" / "live-view.json").write_text(
        json.dumps({
            "version": 1,
            "title": "Unsafe",
            "reason": "Credential files are never renderable.",
            "paths": [".npmrc"],
        }),
        encoding="utf-8",
    )
    write_session_meta(root, SessionMeta(id=sid, cwd=str(life)))

    rows = client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"]

    assert rows == []


def test_html_and_svg_artifacts_are_never_served_as_executable_content(ctx) -> None:
    root, sid, life, client = ctx
    workspace = _seed_result_artifacts(root, sid, life)
    (workspace / "report.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (workspace / "figure.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        encoding="utf-8",
    )
    with (life / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "life.mission.completed",
            "item_id": "result-web-content",
            "success": True,
            "title": "web-shaped evidence",
            "ts": time.time() + 1,
            "planner_report": {"evidence_files": [
                {"path": "report.html"}, {"path": "figure.svg"},
            ]},
        }) + "\n")

    html = client.get(f"/api/projects/{sid}/artifact/raw", params={"path": "report.html"})
    svg = client.get(f"/api/projects/{sid}/artifact/raw", params={"path": "figure.svg"})
    html_info = client.get(
        f"/api/projects/{sid}/artifact",
        params={"path": "report.html"},
    )
    assert html_info.status_code == 200
    assert html_info.json()["kind"] == "html"
    assert html_info.json()["preview"] == "<script>alert(1)</script>"
    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/plain")
    assert html.headers["x-content-type-options"] == "nosniff"
    assert svg.status_code == 404


def test_artifact_metadata_supports_rich_browser_formats(tmp_path: Path) -> None:
    expected = {
        "view.md": "markdown",
        "data.json": "json",
        "events.jsonl": "json",
        "table.csv": "table",
        "table.tsv": "table",
        "sound.mp3": "audio",
        "clip.mp4": "video",
        "image.png": "image",
        "paper.pdf": "pdf",
    }
    for name, kind in expected.items():
        (tmp_path / name).write_bytes(b"x")
        row = artifacts.artifact_metadata(tmp_path, name, preview_bytes=1024)
        assert row is not None and row["kind"] == kind


def test_git_diff_is_workspace_scoped_and_auth_endpoint_ready(ctx) -> None:
    root, sid, life, client = ctx
    workspace = _seed_result_artifacts(root, sid, life)
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(["git", "-C", str(workspace), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(workspace), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(workspace), "add", "paper/result.md"], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "base"], check=True)
    (workspace / "paper" / "result.md").write_text("# Certified\nupdated result\n", encoding="utf-8")

    response = client.get(f"/api/projects/{sid}/git-diff")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert "paper/result.md" in payload["status"]
    assert "updated result" in payload["diff"]
    assert response.headers["cache-control"] == "private, no-store"


# ── write side ───────────────────────────────────────────────────────────

def test_note_appends_user_note_event(ctx) -> None:
    _, sid, _, client = ctx
    r = client.post(f"/api/projects/{sid}/note", json={"text": "randomize the affine each eval"})
    assert r.status_code == 200
    types = [e["type"] for e in client.get(f"/api/projects/{sid}/events").json()["events"]]
    assert "user.note" in types


def test_backlog_dispose_done_and_skip(ctx) -> None:
    root, sid, life, client = ctx
    r = client.post(f"/api/projects/{sid}/backlog/item1/dispose", json={"op": "done"})
    assert r.status_code == 200
    assert r.json()["item"]["status"] != "pending"
    # add another to skip
    LifeMemory.open(life).backlog  # ensure store readable
    (life / "backlog.jsonl").open("a").write(
        json.dumps({"id": "item2", "title": "x", "objective": "x", "status": "pending",
                    "priority": 100, "max_cost_usd": 30.0, "ts": time.time()}) + "\n"
    )
    r2 = client.post(f"/api/projects/{sid}/backlog/item2/dispose", json={"op": "skip"})
    assert r2.json()["item"]["status"] == "skipped"


def test_backlog_stop_disables_iteration(ctx) -> None:
    _, sid, _, client = ctx
    r = client.post(f"/api/projects/{sid}/backlog/item1/stop")
    assert r.status_code == 200
    assert r.json()["item"]["iterate"] is False


def test_unknown_backlog_item_404(ctx) -> None:
    _, sid, _, client = ctx
    assert client.post(f"/api/projects/{sid}/backlog/nope/stop").status_code == 404
    assert client.post(f"/api/projects/{sid}/backlog/nope/dispose", json={"op": "done"}).status_code == 404


def test_wave1_reads_404_on_unknown_project(ctx) -> None:
    _, _, _, client = ctx
    for path in ("status", "journal", "doctor", "config", "identity", "transcript", "backlog/item1"):
        assert client.get(f"/api/projects/s-nope/{path}").status_code == 404, path


# ---------------------------------------------------------------------------
# Lifecycle resume on TEAM Manager dispatch
# ---------------------------------------------------------------------------


def _persist_lifecycle_done(life_dir: Path) -> None:
    """Write a lifecycle.json with state=done so tests can verify resume."""
    from argus_skill.life.project_lifecycle import ProjectState, ProjectStatus
    from argus_skill.life.project_lifecycle_io import write_persisted

    status = ProjectStatus(
        project_id=life_dir.name,
        state=ProjectState.DONE,
        created_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )
    write_persisted(life_dir, status=status, history=[])


def _persist_lifecycle_state(life_dir: Path, state_str: str) -> None:
    """Write a lifecycle.json with an arbitrary state."""
    from argus_skill.life.project_lifecycle import ProjectState, ProjectStatus
    from argus_skill.life.project_lifecycle_io import write_persisted

    status = ProjectStatus(
        project_id=life_dir.name,
        state=ProjectState(state_str),
        created_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )
    write_persisted(life_dir, status=status, history=[])


def _make_mem_with_launch_cwd(tmp_path: Path, sid: str = "s-lifecycle-test"):
    """Create a MemoryBundle with session metadata including launch_cwd."""
    from argus_skill.life.memory import MemoryBundle

    launch_dir = tmp_path / "workspace"
    launch_dir.mkdir(parents=True, exist_ok=True)

    mem = MemoryBundle.for_cwd(
        fingerprint=sid, global_root=tmp_path
    )
    # Write session meta with launch_cwd
    meta = SessionMeta(
        id=sid,
        created=time.time(),
        last_active=time.time(),
        launch_cwd=str(launch_dir),
    )
    write_session_meta(tmp_path, meta)
    # Ensure life dir exists
    mem.project_root.mkdir(parents=True, exist_ok=True)
    return mem, launch_dir


class TestManagerMessageLifecycleErrors:
    """manager_message returns structured error for quarantined/archived projects.

    This is a blocking integration test: it exercises manager_message end-to-end
    with a stubbed Manager front-door so no real model is invoked, and verifies
    that a RuntimeError raised by resume_done_lifecycle_for_team_dispatch is
    caught and converted to ``{"kind": "error", ...}`` — never an unhandled
    exception / HTTP 500.
    """

    @pytest.mark.parametrize("state", ["quarantined", "archived"])
    def test_quarantined_archived_return_structured_error(
        self,
        tmp_path: Path,
        state: str,
        monkeypatch,
    ) -> None:
        from argus_skill.manager import config_intent as ci
        from argus_skill.manager import front_door as fd

        sid = f"s-mgr-err-{state}"
        life_dir = tmp_path / "projects" / sid
        life_dir.mkdir(parents=True, exist_ok=True)
        # Minimal events file so MemoryBundle doesn't error on open.
        (life_dir / "events.jsonl").write_text(
            json.dumps({"type": "mission.started", "text": "x", "ts": time.time()}) + "\n",
            encoding="utf-8",
        )
        # Persist the blocking lifecycle state.
        _persist_lifecycle_state(life_dir, state)

        # Stub the front-door classify so we take the TEAM/complex path
        # without calling a real model (route="complex", no config intent,
        # no control signal).
        monkeypatch.setattr(
            ci,
            "_front_door_classify",
            lambda mem, text, chat_state, **kwargs: (None, None, "complex"),
        )
        # Stub triage to return None → TEAM path (not a chat/SELF reply).
        monkeypatch.setattr(fd, "manager_triage", lambda *a, **kw: None)
        # No active mission — so we don't take the "already running" early-return.
        monkeypatch.setattr(fd, "mission_is_running", lambda mem: False)

        # Clear per-session state cache so this test starts clean.
        manager_bridge._STATES.pop(sid, None)

        result = manager_bridge.manager_message(
            sid, "add a new feature", global_root=tmp_path
        )

        assert result["kind"] == "error", f"expected error response, got {result!r}"
        assert "could not enqueue" in result["reply"]
        assert state in result["reply"]
    """resume_done_lifecycle_for_team_dispatch resumes a done project on TEAM."""

    def test_done_project_resumes_to_active_state(self, tmp_path: Path) -> None:
        from argus_skill.life.project_lifecycle_io import load_persisted
        from argus_skill.manager.dispatch import (
            resume_done_lifecycle_for_team_dispatch,
        )

        mem, _launch = _make_mem_with_launch_cwd(tmp_path)
        _persist_lifecycle_done(mem.project_root)

        result = resume_done_lifecycle_for_team_dispatch(mem)

        assert result is True
        persisted = load_persisted(mem.project_root)
        assert persisted["state"] in {"incubating", "running", "writing"}
        assert persisted["history"][-1]["reason"] == "manager_team_dispatch"

    def test_done_project_resume_uses_launch_cwd(self, tmp_path: Path) -> None:
        """Resume infers observable status from session launch_cwd."""
        from argus_skill.life.project_lifecycle_io import load_persisted
        from argus_skill.manager.dispatch import (
            resume_done_lifecycle_for_team_dispatch,
        )

        mem, launch = _make_mem_with_launch_cwd(tmp_path)
        # Put a paper draft in the workspace to trigger writing state
        paper_dir = launch / "paper"
        paper_dir.mkdir()
        (paper_dir / "main.tex").write_text("\\documentclass{article}", encoding="utf-8")
        _persist_lifecycle_done(mem.project_root)

        result = resume_done_lifecycle_for_team_dispatch(mem)

        assert result is True
        persisted = load_persisted(mem.project_root)
        assert persisted["state"] == "writing"

    @pytest.mark.parametrize("state", ["quarantined", "archived"])
    def test_quarantined_and_archived_raise(
        self, tmp_path: Path, state: str
    ) -> None:
        from argus_skill.manager.dispatch import (
            resume_done_lifecycle_for_team_dispatch,
        )

        mem, _ = _make_mem_with_launch_cwd(tmp_path)
        _persist_lifecycle_state(mem.project_root, state)

        with pytest.raises(RuntimeError, match=state):
            resume_done_lifecycle_for_team_dispatch(mem)

        # State must remain unchanged
        from argus_skill.life.project_lifecycle_io import load_persisted

        persisted = load_persisted(mem.project_root)
        assert persisted["state"] == state

    @pytest.mark.parametrize("state", ["incubating", "running", "writing"])
    def test_active_states_are_noop(
        self, tmp_path: Path, state: str
    ) -> None:
        """Already-active projects return False and stay unchanged."""
        from argus_skill.life.project_lifecycle_io import load_persisted
        from argus_skill.manager.dispatch import (
            resume_done_lifecycle_for_team_dispatch,
        )

        mem, _ = _make_mem_with_launch_cwd(tmp_path)
        _persist_lifecycle_state(mem.project_root, state)

        result = resume_done_lifecycle_for_team_dispatch(mem)

        assert result is False
        persisted = load_persisted(mem.project_root)
        assert persisted["state"] == state

    def test_chat_self_never_mutates_done(self, tmp_path: Path) -> None:
        """chat/SELF classification must not touch lifecycle.

        This test validates the contract: only TEAM dispatch calls the
        resume helper, so a done project stays done for chat/SELF turns.
        """
        from argus_skill.life.project_lifecycle_io import load_persisted

        mem, _ = _make_mem_with_launch_cwd(tmp_path)
        _persist_lifecycle_done(mem.project_root)

        # Simulate what chat/SELF does: nothing — no resume call.
        persisted = load_persisted(mem.project_root)
        assert persisted["state"] == "done"

    def test_no_lifecycle_file_returns_false(self, tmp_path: Path) -> None:
        """Fresh project with no lifecycle.json should be a no-op."""
        from argus_skill.manager.dispatch import (
            resume_done_lifecycle_for_team_dispatch,
        )

        mem, _ = _make_mem_with_launch_cwd(tmp_path)
        # No lifecycle file written

        result = resume_done_lifecycle_for_team_dispatch(mem)

        assert result is False
