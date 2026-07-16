"""Novelty-gate threshold CALIBRATION harness — turns the model-seed defaults
(``_DEFAULTS`` block_run, ``_DEFAULT_OVERLAP_BLOCK``) into MEASURED thresholds.

The block thresholds in :mod:`..novelty` are honestly labelled model-seed
(BCC-pending): nobody has measured their false-positive / recall trade-off on
real text. This harness does exactly that — GIVEN a labelled corpus:

    corpus.json = [
      {"draft": "...", "reference": "...", "language": "zh", "label": "original"},
      {"draft": "...", "reference": "...", "language": "zh", "label": "lifted"},
      ...
    ]

For each (block_run, overlap_ratio) combo it computes, over the corpus:
  * FP rate = fraction of ``original`` samples that BLOCK (false alarms), and
  * recall  = fraction of ``lifted``   samples that BLOCK (catches),
then recommends the combo meeting ``--max-fp`` with the highest recall (ties →
tighter run). It drives the gate through the EXISTING ``novelty_budget`` knobs,
so calibrating needs no code change — only data.

Honesty: with no corpus file this prints BLOCKED and exits without inventing
numbers. ``--demo`` runs a tiny, explicitly SYNTHETIC set purely to prove the
harness itself works — it is NOT a calibration and says so.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from argus_skill.verticals.fiction_writing.novelty import is_original

BLOCK_RUNS = (16, 20, 24, 28, 32)
OVERLAP_RATIOS = (0.3, 0.4, 0.5, 0.6, 0.7)


def _blocks(sample: dict[str, Any], block_run: int, ratio: float) -> bool:
    card = {"novelty_budget": {"max_verbatim_run": block_run, "max_overlap_ratio": ratio}}
    lang = sample.get("language", "zh")
    return not is_original(sample["draft"], sample["reference"], card, lang)


def calibrate(
    samples: list[dict[str, Any]],
    block_runs: tuple[int, ...] = BLOCK_RUNS,
    ratios: tuple[float, ...] = OVERLAP_RATIOS,
) -> list[dict[str, Any]]:
    """Return one row per (block_run, ratio) with measured fp_rate and recall.

    ``fp_rate`` is over ``original`` samples, ``recall`` over ``lifted`` samples.
    Buckets with no samples report a rate of 0.0 (nothing to get wrong / catch).
    """
    originals = [s for s in samples if s.get("label") == "original"]
    lifted = [s for s in samples if s.get("label") == "lifted"]
    rows: list[dict[str, Any]] = []
    for br in block_runs:
        for r in ratios:
            fp = sum(_blocks(s, br, r) for s in originals)
            tp = sum(_blocks(s, br, r) for s in lifted)
            rows.append({
                "block_run": br, "overlap_ratio": r,
                "fp_rate": (fp / len(originals)) if originals else 0.0,
                "recall": (tp / len(lifted)) if lifted else 0.0,
                "n_original": len(originals), "n_lifted": len(lifted),
            })
    return rows


def recommend(rows: list[dict[str, Any]], max_fp: float) -> dict[str, Any] | None:
    """Pick the row with fp_rate <= max_fp maximizing recall (tie → tighter run,
    then lower ratio). None if nothing meets the FP ceiling."""
    ok = [r for r in rows if r["fp_rate"] <= max_fp]
    if not ok:
        return None
    return sorted(ok, key=lambda r: (-r["recall"], r["block_run"], r["overlap_ratio"]))[0]


_DEMO = [
    {"label": "original", "language": "zh", "reference": "黛玉自那日弃舟登岸时便有荣国府打发了轿子",
     "draft": "却说那日风清，黛玉扶着紫鹃缓缓下船，只觉两岸人家与故乡大不相同。"},
    {"label": "lifted", "language": "zh", "reference": "黛玉自那日弃舟登岸时便有荣国府打发了轿子并拉行李的车辆久候了",
     "draft": "却说黛玉自那日弃舟登岸时便有荣国府打发了轿子并拉行李的车辆久候了，一时下了轿。"},
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("corpus", nargs="?", help="path to labelled corpus.json")
    ap.add_argument("--max-fp", type=float, default=0.02, help="false-positive ceiling")
    ap.add_argument("--demo", action="store_true", help="run the SYNTHETIC demo set")
    args = ap.parse_args()

    if args.demo:
        samples = _DEMO
        print("!! DEMO: synthetic 2-sample set — proves the harness runs; NOT a calibration.\n")
    elif args.corpus and Path(args.corpus).is_file():
        samples = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    else:
        print("BLOCKED: no labelled corpus. Provide corpus.json "
              '[{"draft","reference","language","label":"original|lifted"}, ...] '
              "or pass --demo. Refusing to invent calibration numbers.")
        return 0

    rows = calibrate(samples)
    print(f"{'block_run':<10}{'ratio':<7}{'fp_rate':<9}{'recall':<8}")
    for r in rows:
        print(f"{r['block_run']:<10}{r['overlap_ratio']:<7}{r['fp_rate']:<9.2f}{r['recall']:<8.2f}")
    rec = recommend(rows, args.max_fp)
    print(f"\nn_original={rows[0]['n_original']} n_lifted={rows[0]['n_lifted']} | max_fp={args.max_fp}")
    print("recommended:", rec or f"NONE meets fp<= {args.max_fp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
