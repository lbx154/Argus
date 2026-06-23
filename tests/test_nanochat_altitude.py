"""Tests for the nanochat 'search altitude' fact surfacer + its vertical hook.

The block is PURE VISIBILITY (no verdict): it re-surfaces the agent's own
recorded per-attempt ``mean_val_bpb`` so the planner/reviewer can judge
saturation. These tests pin the facts it computes (floor / distance /
consecutive-non-improving / recombined-token hint) and the fail-soft contract.
"""
from __future__ import annotations

import json

from argus_skill.verticals._base import load_vertical, vertical_search_altitude
from argus_skill.verticals.nanochat.stages import (
    _REF_BEST,
    _REF_OPTIMIZED_FROM_VANILLA,
    search_altitude_context,
)


def _write_attempt(root, name: str, mean_val_bpb: float | None, *, csv_vals=None) -> None:
    d = root / "attempts" / name
    d.mkdir(parents=True, exist_ok=True)
    if mean_val_bpb is not None:
        (d / "summary.json").write_text(
            json.dumps({"candidate": name, "mean_val_bpb": mean_val_bpb}),
            encoding="utf-8",
        )
    if csv_vals is not None:
        lines = ["seed,val_bpb"] + [f"{i},{v}" for i, v in enumerate(csv_vals)]
        (d / "results.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_empty_or_missing_attempts_dir_is_failsoft(tmp_path):
    # No attempts/ dir at all → safe empty string, never raises.
    assert search_altitude_context(tmp_path) == ""
    (tmp_path / "attempts").mkdir()
    assert search_altitude_context(tmp_path) == ""  # dir exists but no scored attempts


def test_floor_distance_and_streak(tmp_path):
    # Floor improves at a002, then three non-improving attempts.
    _write_attempt(tmp_path, "a001_seed", 1.00)
    _write_attempt(tmp_path, "a002_win", 0.95)  # best (lowest)
    _write_attempt(tmp_path, "a003_nibble", 0.951)
    _write_attempt(tmp_path, "a004_nibble", 0.952)
    _write_attempt(tmp_path, "a005_nibble", 0.953)
    block = search_altitude_context(tmp_path)

    assert "Attempts scored so far: 5" in block
    assert "0.950000" in block  # the floor
    assert "a002_win" in block  # floor provenance
    # distance to the two reference targets, computed live (not a stale literal)
    assert f"{0.95 - _REF_OPTIMIZED_FROM_VANILLA:+.4f}" in block
    assert f"{0.95 - _REF_BEST:+.4f}" in block
    # three attempts since the floor last improved (a003,a004,a005)
    assert "Consecutive attempts since the FLOOR last improved: 3" in block


def test_token_frequency_surfaces_recombined_levers(tmp_path):
    # The same lever token recombined across attempts is the cargo-cult signal.
    for i in range(4):
        _write_attempt(tmp_path, f"a00{i + 1}_localrawv_bundle", 0.97 + i * 0.001)
    block = search_altitude_context(tmp_path)
    assert "localrawv×4" in block
    assert "bundle×4" in block


def test_results_csv_fallback_when_no_summary(tmp_path):
    # An attempt with only results.csv still contributes its mean.
    _write_attempt(tmp_path, "a001_csvonly", None, csv_vals=[0.90, 0.92])
    block = search_altitude_context(tmp_path)
    assert "Attempts scored so far: 1" in block
    assert "0.910000" in block  # mean of 0.90, 0.92


def test_block_states_no_verdict(tmp_path):
    _write_attempt(tmp_path, "a001_x", 0.97)
    block = search_altitude_context(tmp_path)
    # The block must explicitly disclaim being a decision (philosophy guard).
    assert "NO verdict" in block
    assert "judgment, not the harness" in block


def test_vertical_hook_returns_block_for_nanochat(tmp_path):
    _write_attempt(tmp_path, "a001_x", 0.97)
    mod = load_vertical("nanochat")
    out = vertical_search_altitude(mod, tmp_path)
    assert "Search altitude" in out


def test_vertical_hook_failopen_for_vertical_without_hook(tmp_path):
    # research vertical has no search_altitude_context → empty string, no raise.
    mod = load_vertical("research")
    assert vertical_search_altitude(mod, tmp_path) == ""


def test_vertical_hook_failopen_on_raising_hook():
    class _Boom:
        @staticmethod
        def search_altitude_context(_root):
            raise RuntimeError("boom")

    assert vertical_search_altitude(_Boom(), "/nonexistent") == ""
