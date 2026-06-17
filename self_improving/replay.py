#!/usr/bin/env python3
# argus self-improving build step 3 — OFFLINE COUNTERFACTUAL REPLAY (zero GPU).
# Re-counts the RECORDED rounds under a PROPOSED policy and computes the
# counterfactual best-valid curve B(t). Scores a scaffolding edit for free,
# filtering hallucinated fixes before any GPU is spent.
#
# HONEST LIMIT (off-policy): it can only re-count rounds that ACTUALLY happened.
# If the OLD policy killed a line, that line's later rounds were never recorded,
# so replay UNDER-estimates a "keep the line" edit unless those rounds exist
# elsewhere in the corpus. => necessary, not sufficient (Tier A only).
import json, sys
from pathlib import Path

FLOOR = 80.18  # seed best-valid (s); B(t) starts here


def fp_value(rnd, rule):
    """forward_progress the reviewer WOULD emit for this round under `rule`."""
    if not rnd.get("measured", True):
        return False            # crash / no measurement -> always False
    if rule == "old":
        # old contract: progress only if it actually moved closer (valid & improved)
        return bool(rnd.get("valid") and rnd.get("improved"))
    if rule == "new":
        # new contract: a measured round that advances a declared bold line is
        # progress EVEN IF it regressed (measured-and-reverted bold = good).
        if rnd.get("bold"):
            return True
        return bool(rnd.get("valid") and rnd.get("improved"))
    raise ValueError(rule)


def replay(rounds, policy):
    """Walk rounds in order; kill a line once its stall streak hits threshold;
    drop a killed line's later rounds; return (B_final, budget, killed, flips)."""
    thr = policy["stall_threshold"]
    rule = policy["fp_rule"]
    streak = {}            # line_id -> consecutive fp=False rounds
    dead = set()
    B = FLOOR
    budget = 0.0
    killed, flips = [], []
    for r in rounds:
        lid = r["line_id"]
        if lid in dead:
            continue                                   # line was killed: round never counted
        budget += r.get("budget_s") or ((r.get("train_time_s") or 80.0) * (r.get("n") or 3))
        fp = fp_value(r, rule)
        if r.get("valid") and r.get("train_time_s") and r["train_time_s"] < B:
            B = r["train_time_s"]
        streak[lid] = 0 if fp else streak.get(lid, 0) + 1
        if streak[lid] >= thr and lid not in dead:
            dead.add(lid)
            killed.append({"line_id": lid, "at_round": r["idx"]})
    return {"B_final": round(B, 3), "budget_s": round(budget, 1),
            "n_killed_lines": len(killed), "killed": killed}


def score_edit(rounds, base_policy, new_policy):
    a = replay(rounds, base_policy)
    b = replay(rounds, new_policy)
    return {
        "base": a, "proposed": b,
        "B_delta": round(a["B_final"] - b["B_final"], 3),   # >0 => proposed reaches lower floor
        "verdict": ("PROPOSED BETTER (lower floor)" if b["B_final"] < a["B_final"] - 1e-6
                    else "NEUTRAL on this corpus (no decision flipped)" if b == a
                    else "PROPOSED NOT BETTER"),
    }


# ---- synthetic episode: a bold line wrongly killed under the old contract ----
def synthetic_demo():
    L = "bold_fp8_line"
    rounds = [
        {"idx": 1, "line_id": L, "bold": True, "measured": True, "valid": False, "improved": False, "train_time_s": 78.5, "n": 3},
        {"idx": 2, "line_id": L, "bold": True, "measured": True, "valid": False, "improved": False, "train_time_s": 78.2, "n": 3},
        {"idx": 3, "line_id": L, "bold": True, "measured": True, "valid": False, "improved": False, "train_time_s": 78.1, "n": 3},
        {"idx": 4, "line_id": L, "bold": True, "measured": True, "valid": True,  "improved": True,  "train_time_s": 78.0, "n": 10},
    ]
    base = {"fp_rule": "old", "stall_threshold": 3}
    new = {"fp_rule": "new", "stall_threshold": 3}
    return score_edit(rounds, base, new)


# ---- real corpus: build rounds from the instrument ledger ----
def rounds_from_ledger(instrument_json):
    led = instrument_json["candidate_ledger"]
    best = FLOOR
    rounds = []
    for i, r in enumerate(led, 1):
        tt = r.get("train_time_s")
        measured = tt is not None and r.get("valid") is not None
        improved = bool(r.get("valid") and tt and tt < best)
        if improved:
            best = tt
        bold = bool(r.get("kernels_touched"))   # kernel work = bold; None/False = not
        rounds.append({"idx": i, "line_id": r["name"].rsplit("_2026", 1)[0],
                       "bold": bold, "measured": measured,
                       "valid": bool(r.get("valid")), "improved": improved,
                       "train_time_s": tt, "n": 3})
    return rounds


def main():
    print("=== SYNTHETIC DEMO (a bold line killed under old fp contract) ===")
    print(json.dumps(synthetic_demo(), indent=2))
    if len(sys.argv) > 1:
        data = json.loads(Path(sys.argv[1]).read_text())
        rounds = rounds_from_ledger(data)
        print("\n=== REAL CORPUS (forward_progress fix, old vs new) ===")
        print(json.dumps(score_edit(rounds,
                                    {"fp_rule": "old", "stall_threshold": 8},
                                    {"fp_rule": "new", "stall_threshold": 8}), indent=2))


if __name__ == "__main__":
    main()
