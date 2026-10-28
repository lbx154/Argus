from __future__ import annotations

from pathlib import Path

from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import append_turn
from argus.webapi.artifacts import list_project_artifacts


def test_a_file_a_completion_message_links_is_a_reply_reference_not_a_delivery(tmp_path):
    """Only a receipt's targets are delivered; a link in the words of a reply is the reply's."""
    sid = "s-resumed-output"
    life, workspace = tmp_path / "projects" / sid, tmp_path / "workspace"
    life.mkdir(parents=True)
    workspace.mkdir()
    (workspace / "result.npz").write_bytes(b"previously written, now verified")
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(workspace)))
    append_turn(life, "argus", "Verified [model](result.npz)", metadata={
        "mission_result": True, "success": True,
    })
    rows = list_project_artifacts(sid, global_root=tmp_path)
    assert [(row["path"], row["source"]) for row in rows] == [("result.npz", "reply")]


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
            workspace=workspace, state_root=life, deliverables=[("report.md", "reviewer_accepted")],
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
