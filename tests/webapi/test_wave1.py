"""Wave-1 tests: the read/inspect + backlog-lifecycle endpoints (1:1 with the
Python cockpit's /status /journal /note /doctor /config /identity /transcript
and /done /skip /rm /stop). Real temp project; no daemon needed."""

from __future__ import annotations

import errno
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.session import SessionMeta, read_session_meta, write_session_meta
from argus.daemon import life_worker as daemon_worker
from argus.life.memory import LifeMemory
from argus.webapi import (
    daemon_lifecycle,
    manager_bridge,
    manager_pending_question,
    manager_state,
    server,
)
from tests.hand_off import manager_runner

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
        json.dumps(
            {
                "id": "item1",
                "title": "tune kernel",
                "objective": "tune the kernel",
                "status": "pending",
                "priority": 100,
                "iterate": True,
                "ts": time.time(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (life / "events.jsonl").write_text(
        json.dumps(
            {
                "id": "j1",
                "ts": time.time(),
                "type": "life.mission.completed",
                "success": True,
                "title": "did a thing",
                "summary": "completed a mission",
                "tags": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (life / "transcript.jsonl").write_text(
        json.dumps({"role": "operator", "text": "复现 Task-3", "ts": time.time()})
        + "\n"
        + json.dumps({"role": "argus", "text": "on it", "ts": time.time()})
        + "\n",
        encoding="utf-8",
    )
    return life


@pytest.fixture()
def ctx(tmp_path: Path):
    life = _make_project(tmp_path)
    return tmp_path, "s-w1000001", life, TestClient(server.create_app(global_root=tmp_path))


def test_create_daemon_rejects_an_unavailable_workdir_before_command_submission(
    tmp_path: Path,
) -> None:
    client = TestClient(server.create_app(global_root=tmp_path))

    response = client.post(
        "/api/daemons",
        json={"workdir": str(tmp_path / "missing")},
    )

    assert response.status_code == 400
    assert "workdir is unavailable" in response.json()["detail"]
    assert not (tmp_path / "daemon.commands.jsonl").exists()


def test_launch_cwd_update_preserves_existing_session_name(tmp_path: Path) -> None:
    created = daemon_lifecycle.create_daemon(name="Existing name", global_root=tmp_path)
    original = read_session_meta(tmp_path, created["sid"])
    assert original is not None
    launch = tmp_path / "new-workspace"
    launch.mkdir()

    assert daemon_lifecycle.set_project_launch_cwd(
        created["sid"],
        str(launch),
        global_root=tmp_path,
    )

    meta = read_session_meta(tmp_path, created["sid"])
    assert meta is not None
    assert meta.display_name == "Existing name"
    assert meta.launch_cwd == str(launch.resolve())
    assert meta.workdir == original.workdir


def test_set_project_workdir_takes_the_pipeline_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    created = daemon_lifecycle.create_daemon(global_root=tmp_path)
    workspace = tmp_path / "ordered-workspace"
    workspace.mkdir()
    order: list[str] = []

    @contextmanager
    def pipeline_lock(_root):
        order.append("pipeline")
        yield

    monkeypatch.setattr(
        "argus.manager._session_ops.manager_pipeline_lock",
        pipeline_lock,
    )

    result = daemon_lifecycle.set_project_workdir(
        created["sid"],
        str(workspace),
        global_root=tmp_path,
    )

    assert result is not None and result["ok"] is True
    assert order == ["pipeline"]


def test_set_project_workdir_rejects_live_daemon_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    created = daemon_lifecycle.create_daemon(global_root=tmp_path)
    workspace = tmp_path / "new-workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        daemon_worker,
        'read_daemon_status',
        lambda _path: SimpleNamespace(alive=True, pid=123),
    )

    result = daemon_lifecycle.set_project_workdir(
        created["sid"],
        str(workspace),
        global_root=tmp_path,
    )

    assert result == {
        "ok": False,
        "error": "cannot change workdir while this daemon is running",
    }


def test_set_project_workdir_allows_live_idempotent_rebind(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    created = daemon_lifecycle.create_daemon(
        launch_cwd=str(workspace),
        workdir=str(workspace),
        global_root=tmp_path,
    )
    monkeypatch.setattr(
        daemon_worker,
        'read_daemon_status',
        lambda _path: SimpleNamespace(alive=True, pid=123),
    )

    result = daemon_lifecycle.set_project_workdir(
        created["sid"],
        str(workspace),
        global_root=tmp_path,
    )

    assert result == {
        "ok": True,
        "workdir": str(workspace.resolve()),
        "unchanged": True,
    }


def test_set_project_workdir_rejects_workspace_owned_by_other_daemon(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    created = daemon_lifecycle.create_daemon(global_root=tmp_path)
    monkeypatch.setattr(
        daemon_lifecycle,
        '_active_workspace_owner',
        lambda *_args, **_kwargs: {
            "sid": "s-other",
            "pid": 456,
            "workdir": str(workspace),
        },
    )

    result = daemon_lifecycle.set_project_workdir(
        created["sid"],
        str(workspace),
        global_root=tmp_path,
    )

    assert result == {
        "ok": False,
        "error": "workdir is already owned by active session s-other (pid 456)",
    }


def test_workdir_endpoint_persists_authoritative_execution_root(ctx) -> None:
    root, sid, _life, client = ctx
    workspace = root / "api-workspace"
    workspace.mkdir()

    response = client.post(
        f"/api/projects/{sid}/workdir",
        json={"workdir": str(workspace)},
    )

    assert response.status_code == 200
    assert response.json()["workdir"] == str(workspace.resolve())
    meta = read_session_meta(root, sid)
    assert meta is not None and meta.workdir == str(workspace.resolve())


# ── read/inspect ────────────────────────────────────────────────────────────


def test_status_composite(ctx) -> None:
    _, sid, _, client = ctx
    body = client.get(f"/api/projects/{sid}/status").json()
    assert set(body) >= {
        "identity",
        "backlog_pending",
        "pending_questions",
        "journal",
        "continuous",
        "inbox_pending",
        "daemon",
        "roles",
        "active_role",
    }
    assert len(body["roles"]) == 4
    assert body["backlog_pending"][0]["objective"] == "tune the kernel"
    assert body["continuous"]["enabled"] is False


def test_doctor(ctx) -> None:
    _, sid, _, client = ctx
    d = client.get(f"/api/projects/{sid}/doctor").json()
    assert isinstance(d["checks"], list) and len(d["checks"]) >= 1
    assert all(set(c) == {"name", "ok", "detail", "fix"} for c in d["checks"])
    assert "log_tail" in d


def test_identity(ctx) -> None:
    _, sid, _, client = ctx
    assert isinstance(client.get(f"/api/projects/{sid}/identity").json()["identity"], str)


def test_backlog_item_returns_full_objective(ctx) -> None:
    _, sid, _, client = ctx
    response = client.get(f"/api/projects/{sid}/backlog/item1")
    assert response.status_code == 200
    item = response.json()["item"]
    assert item["id"] == "item1"
    assert item["objective"] == "tune the kernel"
    assert "iteration_cycles_done" in item
    assert client.get(f"/api/projects/{sid}/backlog/nope").status_code == 404


def _seed_result_artifacts(root: Path, sid: str, life: Path) -> Path:
    workspace = root / "workspace"
    (workspace / "paper").mkdir(parents=True)
    (workspace / "paper" / "result.md").write_text("# Certified\nreal result\n", encoding="utf-8")
    (workspace / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (workspace / ".review-note").write_text("hidden evidence\n", encoding="utf-8")
    (workspace / "secret.txt").write_text("not allowlisted", encoding="utf-8")
    outside = root / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    write_session_meta(
        root,
        SessionMeta(id=sid, cwd=str(life), workdir=str(workspace)),
    )
    _write_live_view(
        life,
        [
            "paper/result.md",
            "pyproject.toml",
            "paper/missing.json",
            "./.review-note",
            "../outside.txt",
        ],
        title="Reviewed outputs",
        reason="The Manager selected the operator-facing result files.",
    )
    return workspace


def _write_live_view(
    life: Path,
    paths: list[str],
    *,
    title: str = "Current outputs",
    reason: str = "",
) -> None:
    manifest = life / ".argus" / "live-view.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "title": title,
                "reason": reason,
                "paths": paths,
            }
        ),
        encoding="utf-8",
    )


def test_artifacts_are_manager_allowlisted_and_workspace_confined(ctx) -> None:
    root, sid, life, client = ctx
    _seed_result_artifacts(root, sid, life)

    rows = client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"]
    assert [row["path"] for row in rows] == ["paper/result.md", "pyproject.toml"]
    assert rows[0]["exists"] is True
    assert rows[1]["exists"] is True
    assert all(row["source"] == "manager_live" for row in rows)
    assert (
        client.get(f"/api/projects/{sid}/artifacts").headers["cache-control"] == "private, no-store"
    )

    info = client.get(
        f"/api/projects/{sid}/artifact",
        params={"path": "paper/result.md"},
    )
    assert info.status_code == 200
    assert info.json()["preview"].startswith("# Certified")
    assert info.json()["kind"] == "markdown"
    assert info.headers["cache-control"] == "private, no-store"

    toml = client.get(
        f"/api/projects/{sid}/artifact",
        params={"path": "pyproject.toml"},
    )
    assert toml.status_code == 200
    assert toml.json()["kind"] == "text"

    raw = client.get(
        f"/api/projects/{sid}/artifact/raw",
        params={"path": "paper/result.md"},
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
        "secret.txt",
        "../outside.txt",
        str(root / "outside.txt"),
    ):
        assert (
            client.get(
                f"/api/projects/{sid}/artifact",
                params={"path": forbidden},
            ).status_code
            == 404
        )
    assert (
        client.get(
            f"/api/projects/{sid}/artifact",
            params={"path": "paper/missing.json"},
        ).status_code
        == 404
    )
    hidden = client.get(
        f"/api/projects/{sid}/artifact",
        params={"path": ".review-note"},
    )
    assert hidden.status_code == 404


def test_manager_live_view_recovers_after_legacy_accidental_clear(ctx) -> None:
    root, sid, life, client = ctx
    workspace = root / "legacy-clear-workspace"
    live = workspace / ".argus/live"
    live.mkdir(parents=True)
    (live / "current.md").write_text("# Current\n", encoding="utf-8")
    write_session_meta(
        root,
        SessionMeta(id=sid, cwd=str(life), workdir=str(workspace)),
    )
    with (life / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "manager.live_view.updated",
                    "title": "Current proof",
                    "reason": "Keep the last valid view visible.",
                    "paths": [".argus/live/current.md"],
                    "ts": time.time(),
                }
            )
            + "\n"
        )
        handle.write(
            json.dumps(
                {
                    "type": "manager.live_view.updated",
                    "title": "",
                    "paths": [],
                    "ts": time.time(),
                }
            )
            + "\n"
        )

    rows = client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"]

    assert [row["path"] for row in rows] == [".argus/live/current.md"]
    assert rows[0]["group_title"] == "Current proof"


def test_manager_live_view_honors_explicit_clear_event(ctx) -> None:
    root, sid, life, client = ctx
    workspace = root / "explicit-clear-workspace"
    live = workspace / ".argus/live"
    live.mkdir(parents=True)
    (live / "current.md").write_text("# Current\n", encoding="utf-8")
    write_session_meta(
        root,
        SessionMeta(id=sid, cwd=str(life), workdir=str(workspace)),
    )
    with (life / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "manager.live_view.updated",
                    "title": "Current proof",
                    "paths": [".argus/live/current.md"],
                    "ts": time.time(),
                }
            )
            + "\n"
        )
        handle.write(
            json.dumps(
                {
                    "type": "manager.live_view.updated",
                    "title": "",
                    "paths": [],
                    "explicit_clear": True,
                    "ts": time.time(),
                }
            )
            + "\n"
        )

    assert client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"] == []


def test_manager_live_symlink_cannot_expose_sensitive_workspace_file(
    ctx,
    require_symlink_support,
) -> None:
    root, sid, life, client = ctx
    (life / ".env").write_text("SECRET=do-not-serve\n", encoding="utf-8")
    live = life / ".argus" / "live"
    live.mkdir(parents=True)
    (live / "current.md").symlink_to(life / ".env")
    (life / ".argus" / "live-view.json").write_text(
        json.dumps(
            {
                "version": 1,
                "title": "Unsafe",
                "reason": "Must be rejected after symlink resolution.",
                "paths": [".argus/live/current.md"],
            }
        ),
        encoding="utf-8",
    )
    write_session_meta(root, SessionMeta(id=sid, cwd=str(life)))

    rows = client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"]

    assert rows == []


def test_sensitive_symlink_alias_is_rejected_even_with_safe_target(
    ctx,
    require_symlink_support,
) -> None:
    root, sid, life, client = ctx
    (life / "public.md").write_text("not secret\n", encoding="utf-8")
    (life / "credentials.json").symlink_to(life / "public.md")
    write_session_meta(root, SessionMeta(id=sid, cwd=str(life)))
    _write_live_view(life, ["credentials.json"])

    rows = client.get(f"/api/projects/{sid}/artifacts").json()["artifacts"]

    assert rows == []


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
        json.dumps(
            {
                "id": "item2",
                "title": "x",
                "objective": "x",
                "status": "pending",
                "priority": 100,
                "ts": time.time(),
            }
        )
        + "\n"
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
    assert (
        client.post(f"/api/projects/{sid}/backlog/nope/dispose", json={"op": "done"}).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# Lifecycle resume on TEAM Manager dispatch
# ---------------------------------------------------------------------------


def _persist_lifecycle_done(life_dir: Path) -> None:
    """Write a lifecycle.json with state=done so tests can verify resume."""
    from argus.life.project_lifecycle import ProjectState, ProjectStatus
    from argus.life.project_lifecycle_io import write_persisted

    status = ProjectStatus(
        project_id=life_dir.name,
        state=ProjectState.DONE,
        created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    write_persisted(life_dir, status=status, history=[])


def _persist_lifecycle_state(life_dir: Path, state_str: str) -> None:
    """Write a lifecycle.json with an arbitrary state."""
    from argus.life.project_lifecycle import ProjectState, ProjectStatus
    from argus.life.project_lifecycle_io import write_persisted

    status = ProjectStatus(
        project_id=life_dir.name,
        state=ProjectState(state_str),
        created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    write_persisted(life_dir, status=status, history=[])


def _make_mem_with_launch_cwd(tmp_path: Path, sid: str = "s-lifecycle-test"):
    """Create a MemoryBundle with an explicit shared execution workdir."""
    from argus.life.memory import MemoryBundle

    launch_dir = tmp_path / "workspace"
    launch_dir.mkdir(parents=True, exist_ok=True)

    mem = MemoryBundle.for_cwd(fingerprint=sid, global_root=tmp_path)
    # New sessions persist workdir separately from their internal state root.
    meta = SessionMeta(
        id=sid,
        created=time.time(),
        last_active=time.time(),
        cwd=str(mem.project_root),
        workdir=str(launch_dir),
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
        from argus.manager import front_door as fd

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

        # A Manager that hands the message to the team as a ticket.
        monkeypatch.setattr(fd, "_ensure_manager_runner", lambda *_a: manager_runner())
        # No active mission — so we don't take the "already running" early-return.
        monkeypatch.setattr(fd, "mission_is_running", lambda mem: False)

        # Clear per-session state cache so this test starts clean.
        manager_state._STATES.pop(sid, None)

        result = manager_bridge.manager_message(sid, "add a new feature", global_root=tmp_path)

        assert result["kind"] == "error", f"expected error response, got {result!r}"
        # The failure says what happens next, in the conversation's language.
        assert "Nothing was changed; send it again" in result["reply"]
        assert "nothing was queued or executed" not in result["reply"]
        assert state not in result["reply"]

    """resume_done_lifecycle_for_team_dispatch resumes a done project on TEAM."""

    def test_done_project_resumes_to_active_state(self, tmp_path: Path) -> None:
        from argus.life.project_lifecycle_io import load_persisted
        from argus.manager.dispatch import (
            resume_done_lifecycle_for_team_dispatch,
        )

        mem, _launch = _make_mem_with_launch_cwd(tmp_path)
        _persist_lifecycle_done(mem.project_root)

        result = resume_done_lifecycle_for_team_dispatch(mem)

        assert result is True
        persisted = load_persisted(mem.project_root)
        assert persisted["state"] in {"incubating", "running", "writing"}
        assert persisted["history"][-1]["reason"] == "manager_team_dispatch"


    @pytest.mark.parametrize("state", ["quarantined", "archived"])
    def test_quarantined_and_archived_raise(self, tmp_path: Path, state: str) -> None:
        from argus.manager.dispatch import (
            resume_done_lifecycle_for_team_dispatch,
        )

        mem, _ = _make_mem_with_launch_cwd(tmp_path)
        _persist_lifecycle_state(mem.project_root, state)

        with pytest.raises(RuntimeError, match=state):
            resume_done_lifecycle_for_team_dispatch(mem)

        # State must remain unchanged
        from argus.life.project_lifecycle_io import load_persisted

        persisted = load_persisted(mem.project_root)
        assert persisted["state"] == state

    @pytest.mark.parametrize("state", ["incubating", "running", "writing"])
    def test_active_states_are_noop(self, tmp_path: Path, state: str) -> None:
        """Already-active projects return False and stay unchanged."""
        from argus.life.project_lifecycle_io import load_persisted
        from argus.manager.dispatch import (
            resume_done_lifecycle_for_team_dispatch,
        )

        mem, _ = _make_mem_with_launch_cwd(tmp_path)
        _persist_lifecycle_state(mem.project_root, state)

        result = resume_done_lifecycle_for_team_dispatch(mem)

        assert result is False
        persisted = load_persisted(mem.project_root)
        assert persisted["state"] == state


# ── Dispatch acknowledgement persistence ────────────────────────────────────


@pytest.mark.parametrize(
    ("dispatch_state", "expected"),
    [
        ("queued_after_current", "This starts once the current work is done."),
        ("queued", "Got it, starting now."),
        ("running", "Got it, it's been picked up."),
        ("already_queued", "I didn't add it again"),
    ],
)
def test_dispatch_ack_describes_queue_state(
    tmp_path: Path,
    dispatch_state: str,
    expected: str,
) -> None:
    from argus.webapi.manager_pending_question import record_task_dispatch_ack

    life_dir = tmp_path / "projects" / "s-queue-ack"
    life_dir.mkdir(parents=True)
    result = {
        "kind": "task",
        "daemon_alive": True,
        "daemon": None,
        "dispatch_state": dispatch_state,
        "item": {"status": "running"},
        "reply": None,
    }

    text = record_task_dispatch_ack(
        "s-queue-ack",
        result,
        global_root=tmp_path,
    )

    assert expected in text
    assert "executor already running" not in text


def test_dispatch_ack_surfaces_transcript_write_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Transcript persistence failure is visible while the mission stays queued."""
    sid = "s-ack-fail"
    life_dir = _make_project(tmp_path, sid)
    transcript = life_dir / "transcript.jsonl"
    real_open = Path.open

    def deny_transcript_append(path: Path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if path == transcript and "a" in mode:
            raise OSError(
                errno.ENOSPC,
                os.strerror(errno.ENOSPC),
                str(transcript),
            )
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_transcript_append)
    fragments: list[tuple[str, dict]] = []
    result: dict = {
        "kind": "task",
        "daemon_alive": False,
        "daemon": {"rc": 0, "pid": 99},
        "item": {"id": "item1", "status": "pending"},
        "reply": None,
    }
    text = manager_pending_question.record_task_dispatch_ack(
        sid,
        result,
        global_root=tmp_path,
        on_fragment=lambda kind, payload: fragments.append((kind, payload)),
    )

    assert result["kind"] == "task"
    assert result["ack_error"] == text
    assert result["reply"] == text
    assert text == "The task is saved; only this confirmation couldn't be recorded."
    assert str(transcript) in result["ack_diagnostic"]
    assert f"[Errno {errno.ENOSPC}] {os.strerror(errno.ENOSPC)}" in (
        result["ack_diagnostic"]
    )
    assert str(transcript) not in text
    assert "Errno" not in text
    assert any(
        kind == "delta" and payload.get("text") == text
        for kind, payload in fragments
    )
    queued = LifeMemory.open(life_dir).backlog.all()
    assert len(queued) == 1
    assert queued[0].id == result["item"]["id"]
    assert queued[0].status == "pending"


def test_dispatch_ack_speaks_the_operator_language_without_echoing_the_task(tmp_path: Path) -> None:
    from argus.webapi.manager_pending_question import record_task_dispatch_ack

    (tmp_path / "projects" / "s-ack").mkdir(parents=True)
    result: dict = {
        "kind": "task",
        "daemon_alive": True,
        "daemon": {"rc": 0, "pid": 7},
        "item": {"id": "t1", "title": "请做一个 8 页的 PPT，介绍 Argus。", "status": "pending"},
    }
    text = record_task_dispatch_ack("s-ack", result, global_root=tmp_path)
    assert text == "收到，马上开始。"

    result = {"kind": "task", "daemon_alive": True, "daemon": None,
              "item": {"id": "t2", "title": "Write the release notes", "status": "pending"}}
    text = record_task_dispatch_ack("s-ack", result, global_root=tmp_path)
    assert text == "Got it, starting now."

    result = {"kind": "task", "daemon_alive": True, "daemon": None,
              "item": {"id": "t3", "title": "改 truncate", "status": "parked",
                       "pending_question": "选 A 还是 B？"}}
    text = record_task_dispatch_ack("s-ack", result, global_root=tmp_path)
    assert text == "开始之前需要你定一下：\n选 A 还是 B？"


def test_dispatch_ack_numbers_the_answers_a_question_offers(tmp_path: Path) -> None:
    """The chat reply offers the answers the card does, numbered as the IM notice numbers them."""
    from argus.core.operator_decision import build_operator_decision
    from argus.life.memory import BacklogItem
    from argus.webapi.manager_dispatch import _item_to_dict
    from argus.webapi.manager_pending_question import record_task_dispatch_ack

    (tmp_path / "projects" / "s-ack").mkdir(parents=True)
    item = BacklogItem.new(title="改 truncate", objective="改 truncate", item_id="t4")
    item.pending_question = "选 A 还是 B？"
    item.operator_decision = build_operator_decision(
        item_id=item.id, title=item.title, reason="两条要求冲突", question=item.pending_question,
        options=("A：保留完整单词", "B：输出不变"),
    )
    result = {"kind": "task", "daemon_alive": True, "daemon": None, "item": _item_to_dict(item, item.title)}
    text = record_task_dispatch_ack("s-ack", result, global_root=tmp_path)
    assert text == "开始之前需要你定一下：\n选 A 还是 B？\n\n可选（回复编号即可）：\n1. A：保留完整单词\n2. B：输出不变"


@pytest.mark.parametrize("planner_pending", [False, True])
def test_dispatch_ack_uses_operator_language_when_manager_rewrites_the_title(tmp_path, planner_pending):
    from argus.webapi.manager_pending_question import record_task_dispatch_ack

    result = {
        "kind": "task", "daemon_alive": True,
        "item": None if planner_pending else {"title": "Compare the latency groups", "status": "pending"},
        "dispatch_state": "planner_pending" if planner_pending else "queued",
    }
    reply = record_task_dispatch_ack(
        "s-operator-language", result, global_root=tmp_path, operator_text="继续比较两组延迟",
    )
    assert ("目标已更新" if planner_pending else "收到，马上开始") in reply
