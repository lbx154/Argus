"""Wave-1 tests: the read/inspect + backlog-lifecycle endpoints (1:1 with the
Python cockpit's /status /journal /note /doctor /config /identity /transcript
and /done /skip /rm /stop). Real temp project; no daemon needed."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from argus_skill.core.session import SessionMeta, read_session_meta, write_session_meta
from argus_skill.life.memory import LifeMemory
from argus_skill.webapi import server

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


def test_project_picker_uses_campaign_objective_before_greeting(ctx) -> None:
    root, sid, _, client = ctx
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
        "paper/result.md", "paper/missing.json", ".review-note",
    ]
    assert rows[0]["exists"] is True
    assert rows[1]["exists"] is False
    assert rows[2]["exists"] is True
    assert client.get(f"/api/projects/{sid}/artifacts").headers["cache-control"] == "private, no-store"

    info = client.get(
        f"/api/projects/{sid}/artifact", params={"path": "paper/result.md"},
    )
    assert info.status_code == 200
    assert info.json()["preview"].startswith("# Certified")
    assert info.json()["kind"] == "text"
    assert info.headers["cache-control"] == "private, no-store"

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
    assert hidden.status_code == 200
    assert hidden.json()["path"] == ".review-note"


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
    for response in (html, svg):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert response.headers["x-content-type-options"] == "nosniff"


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
