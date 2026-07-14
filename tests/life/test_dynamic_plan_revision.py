from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.life.memory import Backlog, BacklogItem


def _old_plan(backlog: Backlog) -> tuple[BacklogItem, BacklogItem, BacklogItem]:
    done = backlog.add(
        BacklogItem.new(
            item_id="old-done",
            title="completed evidence",
            objective="preserve this result",
            plan_id="plan-a",
            plan_version=1,
            node_key="done",
        )
    )
    running = backlog.add(
        BacklogItem.new(
            item_id="old-running",
            title="falsified route",
            objective="stop this route",
            plan_id="plan-a",
            plan_version=1,
            node_key="route",
        )
    )
    pending = backlog.add(
        BacklogItem.new(
            item_id="old-pending",
            title="obsolete analysis",
            objective="do not run this after replanning",
            plan_id="plan-a",
            plan_version=1,
            node_key="analysis",
            deps=[running.id],
        )
    )
    backlog.mark_done(done.id)
    backlog.mark_running(running.id)
    return done, running, pending


def _replacement() -> list[BacklogItem]:
    discover = BacklogItem.new(
        item_id="new-discover",
        title="discover replacement",
        objective="test a mechanism distinct from the falsified route",
        plan_id="plan-b",
        plan_version=2,
        node_key="discover",
        context_refs=[
            {
                "kind": "artifact",
                "ref": "research/NO_GO.md",
                "why": "records why plan-a failed",
            }
        ],
    )
    verify = BacklogItem.new(
        item_id="new-verify",
        title="verify replacement",
        objective="independently verify the new mechanism",
        plan_id="plan-b",
        plan_version=2,
        node_key="verify",
        deps=[discover.id],
    )
    return [discover, verify]


def _apply(backlog: Backlog, new_items: list[BacklogItem] | None = None):
    return backlog.apply_plan_revision(
        expected_plan_id="plan-a",
        expected_version=1,
        new_plan_id="plan-b",
        new_version=2,
        supersede_item_ids=["old-running", "old-pending"],
        new_items=new_items or _replacement(),
        reason="verifier evidence invalidated plan-a",
    )


def test_apply_plan_revision_preserves_done_and_replaces_remaining(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    done, running, pending = _old_plan(backlog)

    result = _apply(backlog)

    rows = {item.id: item for item in backlog.all()}
    assert rows[done.id].status == "done"
    for old_id in (running.id, pending.id):
        assert rows[old_id].status == "superseded"
        assert rows[old_id].superseded_by_plan_id == "plan-b"
        assert rows[old_id].superseded_reason == "verifier evidence invalidated plan-a"
        assert rows[old_id].finished_ts is not None
    assert rows["new-discover"].status == "pending"
    assert rows["new-verify"].deps == ["new-discover"]
    assert result.superseded_ids == ("old-running", "old-pending")
    assert result.added_ids == ("new-discover", "new-verify")
    assert [item.id for item in backlog.ready()] == ["new-discover"]


def test_plan_revision_conflict_leaves_file_unchanged(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    _old_plan(backlog)
    before = backlog.path.read_bytes()

    with pytest.raises(RuntimeError, match="plan revision conflict"):
        backlog.apply_plan_revision(
            expected_plan_id="plan-a",
            expected_version=99,
            new_plan_id="plan-b",
            new_version=100,
            supersede_item_ids=["old-running", "old-pending"],
            new_items=_replacement(),
            reason="stale writer",
        )

    assert backlog.path.read_bytes() == before


def test_plan_revision_rejects_partial_replacement(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    _old_plan(backlog)
    before = backlog.path.read_bytes()

    with pytest.raises(ValueError, match="must supersede every active item"):
        backlog.apply_plan_revision(
            expected_plan_id="plan-a",
            expected_version=1,
            new_plan_id="plan-b",
            new_version=2,
            supersede_item_ids=["old-running"],
            new_items=_replacement(),
            reason="partial replacement is unsafe",
        )

    assert backlog.path.read_bytes() == before


def test_plan_revision_rejects_dependency_outside_new_batch(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    _old_plan(backlog)
    invalid = _replacement()
    invalid[1].deps = ["old-done"]
    before = backlog.path.read_bytes()

    with pytest.raises(ValueError, match="outside the replacement batch"):
        _apply(backlog, invalid)

    assert backlog.path.read_bytes() == before


def test_plan_revision_write_failure_keeps_previous_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    _old_plan(backlog)
    before = backlog.path.read_bytes()

    def fail_save(_items) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(backlog, "_save", fail_save)
    with pytest.raises(OSError, match="simulated disk failure"):
        _apply(backlog)

    assert backlog.path.read_bytes() == before


def test_unversioned_items_cannot_be_revised_as_one_implicit_plan(
    tmp_path: Path,
) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    backlog.add(BacklogItem.new(item_id="manual-a", title="A", objective="A"))
    backlog.add(BacklogItem.new(item_id="manual-b", title="B", objective="B"))
    before = backlog.path.read_bytes()

    with pytest.raises(ValueError, match="expected plan id must not be empty"):
        backlog.apply_plan_revision(
            expected_plan_id="",
            expected_version=0,
            new_plan_id="plan-b",
            new_version=1,
            supersede_item_ids=["manual-a", "manual-b"],
            new_items=[
                BacklogItem.new(
                    item_id="new",
                    title="replacement",
                    objective="replacement",
                    plan_id="plan-b",
                    plan_version=1,
                    node_key="replacement",
                )
            ],
            reason="one manual item requested reconsideration",
        )

    assert backlog.path.read_bytes() == before
