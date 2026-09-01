from __future__ import annotations

import os
from pathlib import Path

from argus_skill.core.mission_view import update_mission_view_event
from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.core.transcript import append_turn
from argus_skill.webapi.artifacts import list_project_artifacts


def test_delivery_receipt_makes_only_its_safe_targets_openable(tmp_path: Path) -> None:
    sid = "s-delivery"
    life = tmp_path / "projects" / sid
    workspace = tmp_path / "workspace"
    life.mkdir(parents=True)
    workspace.mkdir()
    (workspace / "final.md").write_text("# Final\n", encoding="utf-8")
    write_session_meta(
        tmp_path,
        SessionMeta(id=sid, cwd=str(life), workdir=str(workspace)),
    )
    update_mission_view_event(life, {
        "type": "life.mission.completed",
        "item_id": "task-1",
        "title": "Deliver final report",
        "objective": "Write the report",
        "success": True,
        "status": "done",
        "summary": "Reviewed report ready.",
        "delivery": {
            "schema_version": 1,
            "delivery_id": "delivery:task-1:task_completed",
            "kind": "task_completed",
            "item_id": "task-1",
            "title": "Deliver final report",
            "summary": "Reviewed report ready.",
            "status": "done",
            "review_status": "done",
            "delivered_at": 1.0,
            "primary_target": {
                "path": "final.md",
                "label": "final.md",
                "source": "reviewer_evidence",
                "why": "Reviewed output.",
            },
            "targets": [
                {
                    "path": "final.md",
                    "label": "final.md",
                    "source": "reviewer_evidence",
                    "why": "Reviewed output.",
                },
                {
                    "path": "../not-allowed.txt",
                    "label": "unsafe",
                    "source": "reviewer_evidence",
                    "why": "must be rejected by artifact confinement",
                },
            ],
        },
    })

    rows = list_project_artifacts(sid, global_root=tmp_path)

    assert rows is not None
    assert [(row["path"], row["source"]) for row in rows] == [
        ("final.md", "delivery"),
    ]
    assert rows[0]["storage_path"] == str((workspace / "final.md").resolve())


def test_completed_legacy_summary_links_become_openable_delivery_files(
    tmp_path: Path,
) -> None:
    sid = "s-summary-delivery"
    life = tmp_path / "projects" / sid
    workspace = tmp_path / "workspace"
    life.mkdir(parents=True)
    workspace.mkdir()
    source = workspace / "survey.tex"
    pdf = workspace / "survey.pdf"
    source.write_text("source", encoding="utf-8")
    pdf.write_bytes(b"pdf")
    write_session_meta(
        tmp_path,
        SessionMeta(id=sid, cwd=str(life), workdir=str(workspace)),
    )
    pdf_link = pdf.resolve().as_posix()
    if os.name == "nt":
        pdf_link = f"/{pdf_link}"
    update_mission_view_event(life, {
        "type": "life.mission.completed",
        "item_id": "task-legacy",
        "title": "Create survey",
        "success": True,
        "status": "done",
        "summary": f"Delivered [survey PDF]({pdf_link}) and `survey.tex`.",
    })

    rows = list_project_artifacts(sid, global_root=tmp_path)

    assert rows is not None
    assert [(row["path"], row["source"]) for row in rows] == [
        ("survey.pdf", "delivery"),
        ("survey.tex", "delivery"),
    ]
    assert all(row["storage_path"] == str(workspace / row["path"]) for row in rows)


def test_solo_transcript_delivery_becomes_openable(tmp_path: Path) -> None:
    sid = "s-solo-delivery"
    life = tmp_path / "projects" / sid
    workspace = tmp_path / "workspace"
    life.mkdir(parents=True)
    workspace.mkdir()
    (workspace / "team.md").write_text("team\n", encoding="utf-8")
    (workspace / "result.txt").write_text("done\n", encoding="utf-8")
    write_session_meta(
        tmp_path,
        SessionMeta(id=sid, cwd=str(life), workdir=str(workspace)),
    )
    update_mission_view_event(life, {
        "type": "life.mission.completed",
        "item_id": "team-task",
        "success": True,
        "status": "done",
        "delivery": {
            "delivery_id": "delivery:team:task_completed",
            "title": "Team result",
            "targets": [{"path": "team.md"}],
        },
    })
    delivery = {
        "delivery_id": "delivery:solo-call:task_completed",
        "title": "Create result",
        "targets": [{
            "path": "result.txt",
            "label": "result.txt",
            "source": "solo_output",
            "why": "Solo output for this completed task.",
        }],
    }
    append_turn(
        life,
        "argus",
        "Created result.txt.",
        metadata={"delivery_id": delivery["delivery_id"], "delivery": delivery},
    )

    rows = list_project_artifacts(sid, global_root=tmp_path)

    assert rows is not None
    assert [(row["path"], row["source"]) for row in rows] == [
        ("team.md", "delivery"),
        ("result.txt", "delivery"),
    ]
