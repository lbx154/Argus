from __future__ import annotations

from pathlib import Path

import pytest

from argus.life.memory import Backlog, BacklogItem


def test_supersede_items_persists_only_named_pending_work(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    first = backlog.add(BacklogItem.new(title="Refuted hypothesis", objective="a"))
    second = backlog.add(BacklogItem.new(title="Related repair", objective="b"))
    unrelated = backlog.add(BacklogItem.new(title="Untested idea", objective="c"))

    superseded = backlog.supersede_items(
        item_ids=[first.id, second.id, "unknown"],
        reason="The evidence refuted this family.",
        superseded_by_plan_id="plan-new",
    )

    assert superseded == (first.id, second.id)
    persisted = Backlog(backlog.path)
    rows = {item.id: item for item in persisted.history()}
    for item_id in superseded:
        assert rows[item_id].status == "superseded"
        assert rows[item_id].finished_ts > 0
        assert rows[item_id].superseded_by_plan_id == "plan-new"
        assert rows[item_id].superseded_reason == "The evidence refuted this family."
    assert [item.id for item in persisted.pending()] == [unrelated.id]


@pytest.mark.parametrize("status", [
    "running", "paused_external_work", "paused_budget", "paused_operator",
    "research_incomplete", "infra_blocked", "done", "failed", "aborted",
    "skipped", "superseded",
])
def test_supersede_items_skips_non_pending_work(tmp_path: Path, status: str) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = backlog.add(BacklogItem.new(title="Existing work", objective="a"))
    backlog.update(item.id, status=status)
    before = backlog.history()[0]

    assert backlog.supersede_items(
        item_ids=[item.id, "unknown"],
        reason="Close this line of work.",
        superseded_by_plan_id="plan-new",
    ) == ()
    assert backlog.history() == [before]


def test_replacement_supersedes_all_pending_work_including_legacy_bootstrap(
    tmp_path: Path,
) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    old_a = backlog.add(BacklogItem.new(title="old a", objective="a"))
    old_b = backlog.add(BacklogItem.new(title="old b", objective="b"))
    legacy_bootstrap = backlog.add(
        BacklogItem.new(
            title="legacy project setup",
            objective="seed",
            tags=["bootstrap", "project"],
        )
    )
    paused = backlog.add(BacklogItem.new(title="paused old work", objective="paused"))
    backlog.update(paused.id, status="paused_daemon_shutdown")
    running = backlog.add(BacklogItem.new(title="running work", objective="running"))
    backlog.update(running.id, status="running")

    superseded = backlog.supersede_pending_for_replacement(
        reason="operator replaced objective",
        replacement_id="intent-new",
    )

    assert set(superseded) == {
        old_a.id,
        old_b.id,
        legacy_bootstrap.id,
        paused.id,
    }
    rows = {item.id: item for item in backlog.all()}
    assert rows[old_a.id].status == "superseded"
    assert rows[old_b.id].superseded_by_plan_id == "intent-new"
    assert rows[legacy_bootstrap.id].status == "superseded"
    assert rows[paused.id].status == "superseded"
    assert rows[running.id].status == "running"
