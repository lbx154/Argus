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
    _no_score_facts,
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


def test_uppercase_mean_val_bpb_key_is_read(tmp_path):
    # The agent later switched the summary.json key to UPPERCASE MEAN_VAL_BPB
    # (with mean_val_bpb left null). The parser must still pick it up — else the
    # newest attempts are silently dropped and the reported floor goes stale.
    d = tmp_path / "attempts" / "a300_newfmt"
    d.mkdir(parents=True)
    (d / "summary.json").write_text(
        json.dumps({"candidate": "a300", "mean_val_bpb": None,
                    "MEAN_VAL_BPB": 0.94, "decision": "promote"}),
        encoding="utf-8",
    )
    # plus an older lowercase attempt that is worse
    _write_attempt(tmp_path, "a299_old", 0.97)
    block = search_altitude_context(tmp_path)
    assert "Attempts scored so far: 2" in block
    assert "0.940000" in block  # the uppercase-keyed score is the floor
    assert "a300_newfmt" in block


def test_score_valid_false_excluded_from_floor(tmp_path):
    # An attempt the agent flagged score_valid=False must NOT seed the floor,
    # even if it carries a (bogus) numeric mean — else distance goes negative.
    d = tmp_path / "attempts" / "a002_invalid"
    d.mkdir(parents=True)
    (d / "summary.json").write_text(
        json.dumps({"mean_val_bpb": 0.40, "score_valid": False,
                    "decision": "discard_infra_failed"}),
        encoding="utf-8",
    )
    _write_attempt(tmp_path, "a001_good", 0.95)
    block = search_altitude_context(tmp_path)
    assert "0.950000" in block       # the valid attempt is the floor
    assert "0.400000" not in block   # the invalid bogus score is excluded
    assert "= -0." not in block      # no negative distance-to-target


def test_non_annn_dir_sorts_as_newest_not_oldest(tmp_path):
    # A stray non-aNNN dir must sort as NEWEST, never as the oldest (which would
    # freeze since_improve and mis-place the recent window).
    for i in range(1, 6):
        _write_attempt(tmp_path, f"a00{i}_x", 1.00 - i * 0.01)  # a005 = 0.95 best
    d = tmp_path / "attempts" / "zz_stray"
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps({"mean_val_bpb": 0.99}), encoding="utf-8")
    block = search_altitude_context(tmp_path)
    assert "0.950000" in block   # a005 is the floor, not the stray
    assert "zz_stray" in block    # the stray appears in the recent (newest) window


def test_floor_anchors_on_promote_not_raw_min(tmp_path):
    # A rejected sub-noise dip BELOW the promoted floor must not be labelled the
    # FLOOR; the agent's promoted attempt is the floor, the dip is noted as raw.
    for name, score, decision in [
        ("a010_promoted", 0.9650, "promote_keep_root"),
        ("a011_rejected_dip", 0.9648, "reject_restore_root"),
    ]:
        d = tmp_path / "attempts" / name
        d.mkdir(parents=True)
        (d / "summary.json").write_text(
            json.dumps({"MEAN_VAL_BPB": score, "decision": decision}),
            encoding="utf-8",
        )
    block = search_altitude_context(tmp_path)
    assert "FLOOR (your latest PROMOTED best): 0.965000" in block
    assert "a010_promoted" in block
    assert "Best RAW measured: 0.964800" in block   # the lower rejected dip
    assert "did not promote it" in block
    assert "since the FLOOR last improved: 1" in block


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


def _write_profiled_attempt(
    root,
    name: str,
    mean_val_bpb: float,
    *,
    summary: dict | None = None,
    curve: dict | None = None,
) -> None:
    """An attempt whose summary.json also carries a measured ``profile`` block."""
    d = root / "attempts" / name
    d.mkdir(parents=True, exist_ok=True)
    obj = {"candidate": name, "mean_val_bpb": mean_val_bpb, "decision": "promote"}
    prof: dict = {"complete": True}
    if summary is not None:
        prof["summary"] = summary
    if curve is not None:
        prof["curve"] = curve
    obj["profile"] = prof
    (d / "summary.json").write_text(json.dumps(obj), encoding="utf-8")


def test_training_dynamics_surfaces_curve_steps_mfu_vram(tmp_path):
    # A profiled attempt → the dynamics block surfaces curve-position, steps,
    # sustained MFU and peak VRAM as MEASURED facts (no verdict).
    _write_profiled_attempt(
        tmp_path,
        "a001_run",
        0.965,
        summary={"num_steps": 2714, "mfu_percent": 41.5, "peak_vram_mb": 49602.2},
        curve={
            "first_loss": 9.21, "first_step": 0,
            "last_loss": 2.70, "last_step": 2713,
            "sampled_curve": [
                {"step": 2048, "loss": 2.87},
                {"step": 2560, "loss": 2.73},
                {"step": 2713, "loss": 2.70},
            ],
        },
    )
    block = search_altitude_context(tmp_path)
    assert "Training dynamics" in block
    assert "NO verdict" in block
    # curve position at the cutoff: final logged loss + the last-interval move
    assert "2.7000@step2713" in block
    assert "steps 2560→2713" in block
    assert "-0.0300" in block  # 2.70 - 2.73 over the last interval
    # throughput / capacity facts
    assert "~2714" in block
    assert "~41.5%" in block
    assert "~48.4 GB" in block  # 49602.2 / 1024
    # philosophy guard: the dynamics facts must NOT prescribe a lever
    assert "your research judgment — not the harness" in block


def test_training_dynamics_absent_without_profile(tmp_path):
    # An attempt with a score but no profile → no dynamics block appended.
    _write_attempt(tmp_path, "a001_noprofile", 0.97)
    block = search_altitude_context(tmp_path)
    assert "Search altitude" in block
    assert "Training dynamics" not in block


def test_training_dynamics_failsoft_on_partial_profile(tmp_path):
    # summary present but curve absent → steps/MFU/VRAM still render, no crash,
    # and no bogus curve line.
    _write_profiled_attempt(
        tmp_path,
        "a001_partial",
        0.965,
        summary={"num_steps": 2700, "mfu_percent": 40.0, "peak_vram_mb": 40960.0},
        curve=None,
    )
    block = search_altitude_context(tmp_path)
    assert "Training dynamics" in block
    assert "~2700" in block
    assert "~40.0 GB" in block
    assert "Train-loss curve" not in block  # no curve data → no curve line


def _write_no_score_attempt(root, name: str, wdelta: float | None) -> None:
    """An attempt the agent's proxy gate skipped (no official score)."""
    d = root / "attempts" / name
    d.mkdir(parents=True, exist_ok=True)
    obj: dict = {"decision": "PROFILE_GATE_FAIL_NO_SCORE", "official_scored": False}
    if wdelta is not None:
        obj["val_rg_all_weighted_delta"] = wdelta
    (d / "summary.json").write_text(json.dumps(obj), encoding="utf-8")


def test_no_score_facts_surfaces_proxy_gated_attempts(tmp_path):
    _write_no_score_attempt(tmp_path, "a010_regime_jump", 0.0036)
    _write_no_score_attempt(tmp_path, "a011_unrecorded", None)
    out = _no_score_facts(tmp_path)
    assert "Proxy-gated NO-SCORE" in out
    assert "NO verdict" in out
    assert "a010_regime_jump" in out
    assert "+0.003600" in out
    assert "(not recorded)" in out          # the wdelta=None case
    assert "research call" in out           # the no-verdict disclaimer


def test_no_score_facts_empty_when_none(tmp_path):
    (tmp_path / "attempts").mkdir()
    assert _no_score_facts(tmp_path) == ""


def test_no_score_block_appended_to_altitude_context(tmp_path):
    # The NO_SCORE block rides along on the full altitude context (which needs at
    # least one SCORED attempt to render at all).
    _write_attempt(tmp_path, "a001_scored", 0.97)
    _write_no_score_attempt(tmp_path, "a002_gated", 0.005)
    block = search_altitude_context(tmp_path)
    assert "Proxy-gated NO-SCORE" in block
    assert "a002_gated" in block
