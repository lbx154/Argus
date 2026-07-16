"""Tests for the novelty calibration harness core (measurement, not fabrication)."""
from __future__ import annotations

from argus_skill.verticals.fiction_writing.evaluations.calibrate_novelty import (
    calibrate,
    recommend,
)

_REF = "黛玉自那日弃舟登岸时便有荣国府打发了轿子并拉行李的车辆久候了"
_SAMPLES = [
    # clean continuation: shares no long run -> should NOT block (false positive if it does)
    {"label": "original", "language": "zh", "reference": _REF,
     "draft": "却说那日风清日暖，黛玉扶着紫鹃缓缓下船，只觉两岸人家与故乡大不相同。"},
    # verbatim lift of a >30-char run -> should block (a miss if it doesn't)
    {"label": "lifted", "language": "zh", "reference": _REF,
     "draft": "却说" + _REF + "，一时下了轿，众人接入。"},
]


def test_calibrate_measures_fp_and_recall():
    rows = calibrate(_SAMPLES, block_runs=(16, 24, 32), ratios=(0.5,))
    assert {r["block_run"] for r in rows} == {16, 24, 32}
    # at a low block_run the lift is caught and the clean draft is not
    low = next(r for r in rows if r["block_run"] == 16 and r["overlap_ratio"] == 0.5)
    assert low["recall"] == 1.0
    assert low["fp_rate"] == 0.0
    assert low["n_original"] == 1 and low["n_lifted"] == 1


def test_recommend_picks_zero_fp_max_recall():
    rows = calibrate(_SAMPLES, block_runs=(16, 24, 32), ratios=(0.4, 0.5, 0.6))
    rec = recommend(rows, max_fp=0.0)
    assert rec is not None
    assert rec["fp_rate"] == 0.0
    assert rec["recall"] == 1.0


def test_recommend_none_when_ceiling_impossible():
    # if we demand recall from a corpus with no lifted samples nothing improves,
    # but an impossible FP ceiling (< 0) yields None
    rows = calibrate(_SAMPLES, block_runs=(24,), ratios=(0.5,))
    assert recommend(rows, max_fp=-1.0) is None
