"""Round-trip tests for the operator/API -> daemon running-item abort mailbox."""
from __future__ import annotations

import json
from pathlib import Path

from argus.life.memory import (
    BacklogItem,
    LifeMemory,
    consume_running_item_abort,
    request_running_item_abort,
)


def _running_item(root: Path) -> BacklogItem:
    backlog = LifeMemory.open(root).backlog
    item = backlog.add(BacklogItem.new(title="task", objective="work"))
    backlog.mark_running(item.id)
    return item


def test_consume_returns_none_when_nothing_written(tmp_path: Path) -> None:
    assert consume_running_item_abort(tmp_path) is None


def test_consume_returns_none_for_falsy_life_dir() -> None:
    assert consume_running_item_abort(None) is None
    assert consume_running_item_abort("") is None


def test_targetless_legacy_request_is_discarded(tmp_path: Path) -> None:
    _running_item(tmp_path)
    path = tmp_path / "running_item_abort.json"
    path.write_text(
        json.dumps({"reason": "stale targetless request"}),
        encoding="utf-8",
    )
    assert consume_running_item_abort(tmp_path) is None
    assert not path.exists()


def test_consume_tolerates_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "running_item_abort.json"
    path.write_text("not valid json{{{", encoding="utf-8")
    assert consume_running_item_abort(tmp_path) is None
    # Still cleaned up so a corrupt file doesn't wedge future requests.
    assert not path.exists()


def test_consume_tolerates_non_dict_json(tmp_path: Path) -> None:
    path = tmp_path / "running_item_abort.json"
    path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    assert consume_running_item_abort(tmp_path) is None


def test_only_targeted_parallel_mission_consumes_abort(tmp_path: Path) -> None:
    backlog = LifeMemory.open(tmp_path).backlog
    first = backlog.add(BacklogItem.new(title="first", objective="work"))
    second = backlog.add(BacklogItem.new(title="second", objective="work"))
    backlog.mark_running(first.id)
    backlog.mark_running(second.id)
    requested, item_id = request_running_item_abort(
        tmp_path,
        reason="stop latest",
    )

    assert requested is True
    assert item_id == second.id
    assert consume_running_item_abort(
        tmp_path,
        target_item_id=first.id,
    ) is None
    assert (tmp_path / "running_item_abort.json").exists()
    assert consume_running_item_abort(
        tmp_path,
        target_item_id=second.id,
    ) == "stop latest"


def test_targeted_abort_cannot_kill_a_later_mission(tmp_path: Path) -> None:
    backlog = LifeMemory.open(tmp_path).backlog
    first = backlog.add(BacklogItem.new(title="first", objective="first"))
    backlog.mark_running(first.id)
    requested, _ = request_running_item_abort(tmp_path, reason="stop first")
    assert requested is True
    backlog.update(first.id, status="failed", finished_ts=1.0)
    second = backlog.add(BacklogItem.new(title="second", objective="second"))
    backlog.mark_running(second.id)

    assert consume_running_item_abort(tmp_path) is None
    assert backlog.all()[-1].status == "running"
