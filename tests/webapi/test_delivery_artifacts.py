from __future__ import annotations

import os
from pathlib import Path

from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import append_turn
from argus.webapi.artifacts import list_project_artifacts


def test_resumed_direct_task_can_link_verified_unchanged_outputs(tmp_path):
    sid = "s-resumed-output"
    life, workspace = tmp_path / "projects" / sid, tmp_path / "workspace"
    life.mkdir(parents=True)
    workspace.mkdir()
    (workspace / "result.npz").write_bytes(b"previously written, now verified")
    os.utime(workspace / "result.npz", (1, 1))
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(workspace)))
    append_turn(life, "argus", "Verified [model](result.npz)", metadata={
        "mission_result": True, "success": True,
    })
    rows = list_project_artifacts(sid, global_root=tmp_path)
    assert [row["path"] for row in rows] == ["result.npz"]


def test_delivery_change_shows_what_a_redelivery_changed(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from argus.life.delivery import build_delivery_receipt
    from argus.webapi import server

    sid = "s-redeliver"
    life = tmp_path / "projects" / sid
    workspace = tmp_path / "workspace"
    life.mkdir(parents=True)
    workspace.mkdir()
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(workspace)))

    def deliver() -> dict:
        receipt = build_delivery_receipt(
            item_id="report", title="report", summary="", success=True, overall_complete=True,
            status="done", review_status="done", final_submission_certified=False,
            workspace=workspace, state_root=life, reviewer_artifacts=["report.md"],
        )
        assert receipt is not None
        return receipt

    (workspace / "report.md").write_text("# Report\nold finding\n", encoding="utf-8")
    first = deliver()
    (workspace / "report.md").write_text("# Report\nnew finding\n", encoding="utf-8")
    second = deliver()

    before, after = first["snapshots"][0]["version"], second["snapshots"][0]["version"]
    with TestClient(server.create_app(global_root=tmp_path, auth_token="test-token")) as client:
        def change(before: str) -> dict:
            response = client.get(
                f"/api/projects/{sid}/delivery-diff",
                params={"path": "report.md", "before": before, "after": after},
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 200
            return response.json()

        assert change(before)["available"] is True
        assert "-old finding" in change(before)["diff"] and "+new finding" in change(before)["diff"]
        missing = change("0" * 32)
        assert missing["available"] is False and missing["diff"] == ""
