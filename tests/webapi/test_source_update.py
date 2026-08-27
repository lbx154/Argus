from __future__ import annotations

from fastapi.testclient import TestClient

from argus_skill.apps.update import UpdateCheck, UpdateResult
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
