"""Leaderboard context steers teammates away from repeated mechanisms.

A recorded best is an incumbent, not a host-certified fixed floor. Teammates
should improve it or try a genuinely different mechanism without the Harness
claiming stronger verification than the shard provides.
"""
from __future__ import annotations

import json

from argus_skill.team import leaderboard


def _shard(tmp_path, **rec):
    d = tmp_path / "shards"
    d.mkdir(exist_ok=True)
    (d / f"{rec['mechanism']}.jsonl").write_text(json.dumps(rec) + "\n")


def test_prefix_marks_best_as_incumbent_without_overclaiming(tmp_path):
    _shard(tmp_path, target="012", mechanism="cutlass_a", metric=5.5, lower_is_better=True)
    _shard(tmp_path, target="012", mechanism="cutlass_b", metric=4.0, lower_is_better=True)
    leaderboard.fold(tmp_path, lower_is_better=True)
    block = leaderboard.objective_block(tmp_path, "012")
    assert "4.0" in block
    assert "current incumbent" in block
    assert "genuinely new mechanism" in block
    assert "FIXED FLOOR" not in block


def test_no_best_still_lists_attempted_mechanisms(tmp_path):
    # An unmeasured attempt has no incumbent but must remain visible as tried.
    _shard(tmp_path, target="x", mechanism="dead", metric=None)
    leaderboard.fold(tmp_path, lower_is_better=True)
    block = leaderboard.objective_block(tmp_path, "x")
    assert "FIXED FLOOR" not in block
    assert "already attempted" in block.lower()
