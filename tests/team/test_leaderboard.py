from __future__ import annotations

import json
from pathlib import Path

from argus_skill.team import _store
from argus_skill.team import leaderboard as lb


def _shard(root: Path, member: str, target: str, metric, mechanism: str,
           success: bool = True, lower=None) -> None:
    d = root / "shards"
    d.mkdir(parents=True, exist_ok=True)
    rec = {
        "member_id": member, "task_id": target, "target": target,
        "success": success, "metric": metric, "mechanism": mechanism,
    }
    if lower is not None:
        rec["lower_is_better"] = lower
    (d / f"{member}.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")


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


def test_fold_per_target_direction_from_shard(tmp_path: Path) -> None:
    # a lower-is-better target (e.g. latency) carries the direction in its shards
    _shard(tmp_path, "w1", "kLat", 10.0, "a", lower=True)
    _shard(tmp_path, "w2", "kLat", 7.0, "b", lower=True)
    board = lb.fold(tmp_path)  # no global flag set
    assert board["kLat"]["best"] == {"mechanism": "b", "metric": 7.0}  # min wins


def test_fold_per_target_overrides_global_default(tmp_path: Path) -> None:
    # ONE fold, two targets with opposite directions — both correct
    _shard(tmp_path, "w1", "kHigh", 1.0, "a")            # no flag → higher-better default
    _shard(tmp_path, "w2", "kHigh", 3.0, "b")
    _shard(tmp_path, "w3", "kLow", 10.0, "c", lower=True)
    _shard(tmp_path, "w4", "kLow", 6.0, "d", lower=True)
    board = lb.fold(tmp_path)
    assert board["kHigh"]["best"]["metric"] == 3.0       # higher-better
    assert board["kLow"]["best"]["metric"] == 6.0        # lower-better (per-target)


def test_fold_falls_back_to_env_when_no_per_target(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_LEADERBOARD_LOWER_IS_BETTER", "1")
    _shard(tmp_path, "w1", "k", 10.0, "a")               # no lower_is_better in shard
    _shard(tmp_path, "w2", "k", 7.0, "b")
    assert lb.fold(tmp_path)["k"]["best"]["metric"] == 7.0  # env global applies


def test_objective_block_lists_best_and_tried(tmp_path: Path) -> None:
    _shard(tmp_path, "w1", "kA", 1.5, "fuse")
    _shard(tmp_path, "w2", "kA", 1.9, "persistent")
    lb.fold(tmp_path)
    block = lb.objective_block(tmp_path, "kA")
    low = block.lower()
    assert "build on the best" in low                   # neutral, domain-agnostic framing
    assert "re-derive" not in low and "depth" not in low  # no optimization-search ritual
    assert "persistent" in block and "fuse" in block and "1.9" in block


def test_objective_block_empty_for_unknown_target(tmp_path: Path) -> None:
    lb.fold(tmp_path)
    assert lb.objective_block(tmp_path, "nope") == ""
