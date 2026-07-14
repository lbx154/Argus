from __future__ import annotations

from argus_skill.life.memory import Backlog, BacklogItem


def test_legacy_backlog_row_defaults_dynamic_plan_metadata() -> None:
    item = BacklogItem.from_jsonable(
        {
            "id": "legacy",
            "ts": 1.0,
            "title": "legacy task",
            "objective": "keep old behavior",
            "status": "pending",
        }
    )

    assert item.plan_id == ""
    assert item.plan_version == 0
    assert item.node_key == ""
    assert item.context_refs == []
    assert item.superseded_by_plan_id == ""
    assert item.superseded_reason == ""


def test_dynamic_plan_metadata_roundtrips_through_backlog_json() -> None:
    item = BacklogItem.new(
        title="audit route",
        objective="inspect the falsified route",
        plan_id="plan-a",
        plan_version=3,
        node_key="audit",
        context_refs=[
            {
                "kind": "artifact",
                "ref": "research/NO_GO.md",
                "why": "contains the falsifier",
                "content_hash": "abc123",
            }
        ],
    )
    item.status = "superseded"
    item.superseded_by_plan_id = "plan-b"
    item.superseded_reason = "new evidence invalidated plan-a"

    restored = BacklogItem.from_jsonable(item.to_jsonable())

    assert restored.plan_id == "plan-a"
    assert restored.plan_version == 3
    assert restored.node_key == "audit"
    assert restored.context_refs == [
        {
            "kind": "artifact",
            "ref": "research/NO_GO.md",
            "why": "contains the falsifier",
            "content_hash": "abc123",
        }
    ]
    assert restored.status == "superseded"
    assert restored.superseded_by_plan_id == "plan-b"
    assert restored.superseded_reason == "new evidence invalidated plan-a"


def test_superseded_dependency_cascade_skips_dependent(tmp_path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    old = backlog.add(
        BacklogItem.new(
            title="old route",
            objective="try old route",
            plan_id="plan-a",
            plan_version=1,
            node_key="old",
        )
    )
    backlog.add(
        BacklogItem.new(
            title="old analysis",
            objective="analyze old route",
            plan_id="plan-a",
            plan_version=1,
            node_key="analysis",
            deps=[old.id],
        )
    )
    backlog.update(
        old.id,
        status="superseded",
        superseded_by_plan_id="plan-b",
        superseded_reason="replacement plan",
    )

    assert backlog.claim_next() is None
    rows = {item.title: item for item in backlog.all()}
    assert rows["old analysis"].status == "skipped"
    assert "superseded" in rows["old analysis"].last_error
