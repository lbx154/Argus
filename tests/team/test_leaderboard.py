from __future__ import annotations

import json
from pathlib import Path

from argus_skill.team import _store
from argus_skill.team import leaderboard as lb


def _shard(root: Path, member: str, target: str, metric, mechanism: str,
           success: bool = True) -> None:
    d = root / "shards"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{member}.jsonl").write_text(json.dumps({
        "member_id": member, "task_id": target, "target": target,
        "success": success, "metric": metric, "mechanism": mechanism,
    }) + "\n", encoding="utf-8")


def test_fold_best_per_target_higher_is_better(tmp_path: Path) -> None:
    _shard(tmp_path, "w1", "kA", 1.5, "fuse")
    _shard(tmp_path, "w2", "kA", 1.9, "persistent")
    _shard(tmp_path, "w3", "kB", 2.1, "tile")
    board = lb.fold(tmp_path)
    assert board["kA"]["best"] == {"mechanism": "persistent", "metric": 1.9}
    assert board["kB"]["best"]["metric"] == 2.1


def test_fold_lower_is_better(tmp_path: Path) -> None:
    _shard(tmp_path, "w1", "kA", 10.0, "a")
    _shard(tmp_path, "w2", "kA", 7.0, "b")
    board = lb.fold(tmp_path, lower_is_better=True)
    assert board["kA"]["best"] == {"mechanism": "b", "metric": 7.0}


def test_fold_null_metric_is_attempt_not_best(tmp_path: Path) -> None:
    _shard(tmp_path, "w1", "kA", None, "unmeasured")
    _shard(tmp_path, "w2", "kA", 1.0, "measured")
    board = lb.fold(tmp_path)
    assert board["kA"]["best"] == {"mechanism": "measured", "metric": 1.0}
    assert {a["mechanism"] for a in board["kA"]["attempts"]} == {"unmeasured", "measured"}


def test_fold_dedups_mechanism_keeping_best(tmp_path: Path) -> None:
    _shard(tmp_path, "w1", "kA", 1.0, "fuse")
    _shard(tmp_path, "w2", "kA", 1.7, "fuse")  # same mechanism tried again, better
    board = lb.fold(tmp_path)
    fuse = [a for a in board["kA"]["attempts"] if a["mechanism"] == "fuse"]
    assert len(fuse) == 1 and fuse[0]["metric"] == 1.7


def test_fold_tolerates_corrupt_shard(tmp_path: Path) -> None:
    _shard(tmp_path, "w1", "kA", 1.0, "ok")
    (tmp_path / "shards" / "bad.jsonl").write_text("{not json", encoding="utf-8")
    board = lb.fold(tmp_path)
    assert board["kA"]["best"]["metric"] == 1.0


def test_fold_writes_leaderboard_json(tmp_path: Path) -> None:
    _shard(tmp_path, "w1", "kA", 1.0, "ok")
    lb.fold(tmp_path)
    assert "kA" in _store.read_json(tmp_path / "leaderboard.json")


def test_fold_empty_when_no_shards(tmp_path: Path) -> None:
    assert lb.fold(tmp_path) == {}
