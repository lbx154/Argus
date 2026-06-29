"""Leaderboard objective_block must steer a fresh teammate OFF archaeology.

The failure mode (observed live): a teammate burns its whole 16-round mission
re-scoring / re-auditing old attempts to re-confirm a floor it already has,
chasing run-to-run reproducibility, and never ships a new mechanism. The
prefix prepended to its objective must say: the verified best is a FIXED FLOOR,
don't reproduce it, beat it.
"""
from __future__ import annotations

import json

from argus_skill.team import leaderboard


def _shard(tmp_path, **rec):
    d = tmp_path / "shards"
    d.mkdir(exist_ok=True)
    (d / f"{rec['mechanism']}.jsonl").write_text(json.dumps(rec) + "\n")


def test_prefix_marks_best_as_fixed_floor_and_forbids_rescore(tmp_path):
    _shard(tmp_path, target="012", mechanism="cutlass_a", metric=5.5, lower_is_better=True)
    _shard(tmp_path, target="012", mechanism="cutlass_b", metric=4.0, lower_is_better=True)
    leaderboard.fold(tmp_path, lower_is_better=True)
    block = leaderboard.objective_block(tmp_path, "012")
    assert "4.0" in block                       # best surfaced
    assert "FIXED FLOOR" in block               # treated as given
    assert "do NOT re-score" in block.lower() or "do not re-score" in block.lower()
    assert "BEATING it" in block                # mission = beat, not reproduce


def test_no_best_no_archaeology_clause(tmp_path):
    # unmeasured attempt only -> no best -> no fixed-floor clause, but still "tried" list
    _shard(tmp_path, target="x", mechanism="dead", metric=None)
    leaderboard.fold(tmp_path, lower_is_better=True)
    block = leaderboard.objective_block(tmp_path, "x")
    assert "FIXED FLOOR" not in block
    assert "already attempted" in block.lower()
