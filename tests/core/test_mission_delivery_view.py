from __future__ import annotations

from argus.core.mission_view import (
    MISSION_VIEW_SCHEMA_VERSION,
    load_mission_view,
    update_mission_view_event,
)


def test_completed_mission_persists_delivery_receipt_in_mission_view(tmp_path) -> None:
    delivery = {
        "schema_version": 1,
        "delivery_id": "delivery:item-1:task_completed",
        "kind": "task_completed",
        "item_id": "item-1",
        "title": "Deliver report",
        "summary": "Final report reviewed.",
        "status": "done",
        "review_status": "done",
        "delivered_at": 1.0,
        "primary_target": {
            "path": "out/final.md",
            "label": "final.md",
            "source": "reviewer_evidence",
            "why": "Reviewer accepted it.",
        },
        "targets": [{
            "path": "out/final.md",
            "label": "final.md",
            "source": "reviewer_evidence",
            "why": "Reviewer accepted it.",
        }],
    }
    update_mission_view_event(tmp_path, {
        "type": "life.mission.started",
        "item_id": "item-1",
        "title": "Deliver report",
        "objective": "Write final report",
        "ts": 1.0,
    })
    view = update_mission_view_event(tmp_path, {
        "type": "life.mission.completed",
        "item_id": "item-1",
        "title": "Deliver report",
        "objective": "Write final report",
        "success": True,
        "status": "done",
        "summary": "Final report reviewed.",
        "outcome": {
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "not_assessed",
            "interruption_kind": "none",
            "resumable": False,
        },
        "delivery": delivery,
        "ts": 2.0,
    })

    assert view["schema_version"] == MISSION_VIEW_SCHEMA_VERSION
    assert view["delivery"] == delivery
    assert load_mission_view(tmp_path)["delivery"] == delivery
