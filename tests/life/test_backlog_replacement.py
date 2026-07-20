from __future__ import annotations

from pathlib import Path

from argus_skill.life.memory import Backlog, BacklogItem


def test_replacement_supersedes_pending_work_but_preserves_bootstrap(
    tmp_path: Path,
) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    old_a = backlog.add(BacklogItem.new(title="old a", objective="a"))
    old_b = backlog.add(BacklogItem.new(title="old b", objective="b"))
    bootstrap = backlog.add(
        BacklogItem.new(
            title="bootstrap empty project root",
            objective="seed",
            tags=["bootstrap", "project"],
        )
    )

    superseded = backlog.supersede_pending_for_replacement(
        reason="operator replaced objective",
        replacement_id="intent-new",
    )

    assert set(superseded) == {old_a.id, old_b.id}
    rows = {item.id: item for item in backlog.all()}
    assert rows[old_a.id].status == "superseded"
    assert rows[old_b.id].superseded_by_plan_id == "intent-new"
    assert rows[bootstrap.id].status == "pending"
