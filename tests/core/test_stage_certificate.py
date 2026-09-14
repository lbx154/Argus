from __future__ import annotations

from types import SimpleNamespace

from argus.core.stage_certificate import (
    latest_stage_review,
    record_stage_review,
)


def _item():
    return SimpleNamespace(
        id="task-one",
        acceptance_check="pytest -q",
        context_refs=[{
            "kind": "file",
            "ref": "result.json",
            "content_hash": "sha256:" + "a" * 64,
        }],
    )


def test_records_host_owned_stage_review(tmp_path) -> None:
    record = record_stage_review(
        state_root=tmp_path / "life",
        project_root=tmp_path,
        stage="baseline",
        item=_item(),
        manager_action="hold",
        manager_reason="repair required",
    )

    assert record["review_status"] == "done"
    assert record["certified"] is False
    assert record["manager_reason"] == "repair required"
    assert latest_stage_review(tmp_path / "life", "baseline") == record


def test_advance_marks_certificate_certified(tmp_path) -> None:
    record = record_stage_review(
        state_root=tmp_path / "life",
        project_root=tmp_path,
        stage="environment",
        item=_item(),
        manager_action="advance",
    )

    assert record["certified"] is True
