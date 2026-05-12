#!/usr/bin/env python3
"""Live progress monitor + final result summarizer for v12-redux fullbench.

Usage:
    python benchmarks/v12_redux_progress.py [--watch SEC]

Prints:
    * trial count: completed / total
    * running reward (rolling) vs v12 baseline (0.5955)
    * flags the 4 v12 known-false-positive trials when they finish so we
      can verify the new raw-evidence pipeline actually catches them
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(
    "/home/argustest/argus-skill/benchmarks/results/tb2-fullbench-2026-05-22-v12-redux"
)
V12_BASELINE_REWARD = 0.5955
V12_BASELINE_COST_PER_TRIAL = 0.139
V12_KNOWN_FALSE_POSITIVES = {
    "mailman",
    "headless-terminal",
    "pytorch-model-cli",
    "path-tracing-reverse",
}


def iter_trial_results() -> list[dict]:
    out: list[dict] = []
    jobs_root = ROOT / "argus-skill-codex" / "jobs"
    if not jobs_root.exists():
        return out
    for job_dir in jobs_root.iterdir():
        if not job_dir.is_dir():
            continue
        for trial_dir in job_dir.iterdir():
            if not trial_dir.is_dir():
                continue
            result_path = trial_dir / "result.json"
            if not result_path.exists():
                continue
            try:
                result = json.loads(result_path.read_text())
            except Exception:
                continue
            if not (trial_dir / "agent").exists() and not (trial_dir / "trial.log").exists():
                continue
            result["_trial_id"] = trial_dir.name
            out.append(result)
    return out


def trial_reward(r: dict) -> float | None:
    # TB v2 result.json shape: verifier_result.rewards.reward (canonical)
    vr = r.get("verifier_result")
    if isinstance(vr, dict):
        rewards = vr.get("rewards")
        if isinstance(rewards, dict) and "reward" in rewards:
            try:
                return float(rewards["reward"])
            except Exception:
                pass
    # Fallback for older / non-TB shapes
    if "reward" in r:
        try:
            return float(r["reward"])
        except Exception:
            return None
    return None


def trial_cost_usd(r: dict) -> float | None:
    ar = r.get("agent_result")
    if isinstance(ar, dict):
        try:
            return float(ar.get("cost_usd"))
        except Exception:
            return None
    return None


def summarize(trials: list[dict]) -> dict:
    rewards = [trial_reward(t) for t in trials]
    costs = [trial_cost_usd(t) for t in trials]
    finished = [r for r in rewards if r is not None]
    paid = [c for c in costs if c is not None]
    return {
        "total_finished": len(finished),
        "rewarded_count": sum(1 for r in finished if r and r > 0),
        "rolling_reward": (sum(finished) / len(finished)) if finished else 0.0,
        "rolling_cost_per_trial": (sum(paid) / len(paid)) if paid else 0.0,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--watch", type=int, default=0)
    args = p.parse_args()

    while True:
        trials = iter_trial_results()
        s = summarize(trials)
        n = s["total_finished"]
        print(
            f"[{time.strftime('%H:%M:%S')}] finished={n}/89  "
            f"reward={s['rolling_reward']:.4f}  "
            f"(v12 {V12_BASELINE_REWARD:.4f}, "
            f"Δ={s['rolling_reward'] - V12_BASELINE_REWARD:+.4f})  "
            f"$/trial={s['rolling_cost_per_trial']:.4f}  "
            f"(v12 ${V12_BASELINE_COST_PER_TRIAL:.4f})  "
            f"pass={s['rewarded_count']}",
            flush=True,
        )
        flagged = []
        for t in trials:
            short = t["_trial_id"].split("__")[0]
            if short in V12_KNOWN_FALSE_POSITIVES:
                r = trial_reward(t)
                if r is not None:
                    flagged.append((short, r))
        if flagged:
            print(
                "  v12 false-positives:",
                ", ".join(f"{name}={r}" for name, r in flagged),
                flush=True,
            )

        if n >= 89:
            rewards = [trial_reward(t) for t in trials if trial_reward(t) is not None]
            mean = sum(rewards) / len(rewards) if rewards else 0.0
            print("\n=== FINAL ===")
            print(f"  reward        : {mean:.4f}  (v12 baseline {V12_BASELINE_REWARD:.4f})")
            print(f"  delta vs v12  : {mean - V12_BASELINE_REWARD:+.4f}")
            print(f"  cost / trial  : ${s['rolling_cost_per_trial']:.4f}  (v12 ${V12_BASELINE_COST_PER_TRIAL:.4f})")
            return 0
        if not args.watch:
            return 0
        time.sleep(args.watch)


if __name__ == "__main__":
    sys.exit(main())
