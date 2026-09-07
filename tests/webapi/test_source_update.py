from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from argus_skill.apps.update import UpdateCheck, UpdateError, UpdateResult
from argus_skill.webapi import server, source_update


def _status(**overrides):
    return {
        "schema_version": 1,
        "state": "current",
        "phase": "complete",
        "running": False,
        "source_root": "/src/Argus",
        "upstream": "lbx154/Argus/main",
        "current_revision": "abc",
        "upstream_revision": "abc",
        "branch": "main",
        "dirty": False,
        "can_update": True,
        "update_available": False,
        "changed": False,
        "restart_required": False,
        "message": "current",
        "error": "",
        "started_at": None,
        "checked_at": 1.0,
        "updated_at": 1.0,
        **overrides,
    }


def test_source_update_routes_are_authenticated_and_dispatch_jobs(
    tmp_path, monkeypatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(server, "read_source_update_status", lambda _root: _status())
    monkeypatch.setattr(
        server,
        "start_source_update",
        lambda _root, *, action: calls.append(action) or _status(
            state="checking" if action == "check" else "updating",
            running=True,
        ),
    )
    client = TestClient(server.create_app(global_root=tmp_path, auth_token="secret"))

    assert client.get("/api/runtime/source-update").status_code == 401
    headers = {"Authorization": "Bearer secret"}
    assert client.get("/api/runtime/source-update", headers=headers).json()["state"] == "current"
    assert client.post("/api/runtime/source-update/check", headers=headers).json()["state"] == "checking"
    assert client.post("/api/runtime/source-update/apply", headers=headers).json()["state"] == "updating"
    assert calls == ["check", "apply"]


def test_noop_source_update_finishes_without_duplicate_status_fields(
    tmp_path, monkeypatch,
) -> None:
    checkout = tmp_path / "source"
    checkout.mkdir()
    revision = "a" * 40
    monkeypatch.setattr(
        source_update,
        "inspect_source_checkout",
        lambda _root: UpdateCheck(
            root=checkout,
            upstream="lbx154/Argus/main",
            current_revision=revision,
            upstream_revision=revision,
            branch="main",
            dirty=False,
        ),
    )
    monkeypatch.setattr(
        source_update,
        "update_source_checkout",
        lambda _root, *, python_executable, on_progress: (
            on_progress("validating"),
            on_progress("pulling"),
            on_progress("complete"),
            UpdateResult(
                root=checkout,
                upstream="lbx154/Argus/main",
                before_revision=revision,
                after_revision=revision,
            ),
        )[-1],
    )

    source_update._run_source_update(tmp_path / "state", "apply", checkout=checkout)

    status = source_update.read_source_update_status(tmp_path / "state")
    assert status["state"] == "succeeded"
    assert status["current_revision"] == revision
    assert status["upstream_revision"] == revision
    assert status["changed"] is False
    assert status["error"] == ""


def test_source_update_recovers_interrupted_worker_status(tmp_path, monkeypatch) -> None:
    source_update._write_status(tmp_path, _status(
        state="updating", running=True, worker_pid=12345,
    ))
    monkeypatch.setattr(source_update, "is_pid_running", lambda _pid: False)

    status = source_update.read_source_update_status(tmp_path)
    assert status["state"] == "failed"
    assert status["phase"] == "interrupted"
    assert status["running"] is False
    assert "worker exited" in status["error"]


def test_source_check_preserves_pending_restart(tmp_path, monkeypatch) -> None:
    source_update._write_status(tmp_path, _status(restart_required=True, changed=True))
    monkeypatch.setattr(source_update, "inspect_source_checkout", lambda _root: UpdateCheck(
        root=tmp_path, upstream="lbx154/Argus/main",
        current_revision="new", upstream_revision="new", branch="main", dirty=False,
    ))

    source_update._run_source_update(tmp_path, "check", checkout=tmp_path)
    status = source_update.read_source_update_status(tmp_path)
    assert status["restart_required"] is True
    assert status["changed"] is True


def test_source_update_install_failure_does_not_claim_source_unchanged(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(source_update, "inspect_source_checkout", lambda _root: UpdateCheck(
        root=tmp_path, upstream="lbx154/Argus/main",
        current_revision="old", upstream_revision="new", branch="main", dirty=False,
    ))
    def fail_install(_root, *, python_executable, on_progress):
        on_progress("installing")
        raise UpdateError(
            "pip install failed after source fast-forward",
            result=UpdateResult(
                root=tmp_path, upstream="lbx154/Argus/main",
                before_revision="old", after_revision="new",
            ),
        )
    monkeypatch.setattr(source_update, "update_source_checkout", fail_install)

    source_update._run_source_update(tmp_path, "apply", checkout=tmp_path)
    status = source_update.read_source_update_status(tmp_path)
    assert status["state"] == "failed"
    assert "pip install failed" in status["error"]
    assert "did not change" not in status["message"]
    assert status["current_revision"] == "new"
    assert status["changed"] is True
    assert status["restart_required"] is True


def test_source_update_reinstallation_requires_restart_without_git_change(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(source_update, "inspect_source_checkout", lambda _root: UpdateCheck(
        root=tmp_path, upstream="microsoft/ArgusAgent/main",
        current_revision="same", upstream_revision="same", branch="main", dirty=False,
    ))
    monkeypatch.setattr(source_update, "update_source_checkout", lambda _root, **_kwargs: UpdateResult(
        root=tmp_path, upstream="microsoft/ArgusAgent/main",
        before_revision="same", after_revision="same", installed=True,
    ))
    source_update._run_source_update(tmp_path, "apply", checkout=tmp_path)
    status = source_update.read_source_update_status(tmp_path)
    assert status["state"] == "succeeded"
    assert status["changed"] is False
    assert status["restart_required"] is True
    assert "Latest source installed" in status["message"]


def test_source_update_thread_smoke_serializes_requests_and_persists_result(
    tmp_path, monkeypatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    def check(_root):
        started.set()
        assert release.wait(5)
        return UpdateCheck(
            root=tmp_path, upstream="lbx154/Argus/main",
            current_revision="old", upstream_revision="new", branch="main", dirty=False,
        )
    monkeypatch.setattr(source_update, "source_root", lambda: tmp_path)
    monkeypatch.setattr(source_update, "inspect_source_checkout", check)

    status = source_update.start_source_update(tmp_path, action="check")
    thread = source_update._THREADS[str(source_update._status_path(tmp_path))]
    try:
        assert started.wait(5)
        assert status["running"] is True
        duplicate = source_update.start_source_update(tmp_path, action="apply")
        assert duplicate["running"] is True
        assert source_update._THREADS[str(source_update._status_path(tmp_path))] is thread
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    result = source_update.read_source_update_status(tmp_path)
    assert result["state"] == "available"
    assert result["running"] is False
