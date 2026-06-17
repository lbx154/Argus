#!/usr/bin/env python3
# argus self-improving build step 2 — PASSIVE INSTRUMENTATION (deterministic, read-only).
# Extracts a structured trajectory/ledger from a mission's experiments/ + a daemon
# life_dir, so the read-only Meta-Critic has hard features to reason over instead of
# raw logs. Emits NOTHING actionable; pure observation.
#
# usage: instrument.py <mission_dir> <life_dir> [baseline_train.py baseline_kernels.py]
import json, re, sys, difflib
from pathlib import Path

SCORE_RE = re.compile(
    r"SCORE valid=(\w+) n=(\d+) val_loss=([\d.]+) p\(mean<3\.28\)=([\d.eE+-]+) train_time=([\d.]+)s")
NUMTOK_RE = re.compile(r"[-+]?\d*\.?\d+")


def changed_lines(a_path, b_path):
    try:
        a = Path(a_path).read_text().splitlines()
        b = Path(b_path).read_text().splitlines()
    except Exception:
        return None
    adds = [l[1:] for l in difflib.unified_diff(a, b, n=0) if l.startswith("+") and not l.startswith("+++")]
    dels = [l[1:] for l in difflib.unified_diff(a, b, n=0) if l.startswith("-") and not l.startswith("---")]
    return {"added": adds, "n_changed": len(adds) + len(dels)}


def is_single_knob(train_diff, kernels_touched):
    # single-knob = no kernel change AND the train.py change is a tiny numeric tweak
    if kernels_touched or train_diff is None:
        return False
    numeric_only = all(
        # each added/removed line differs from baseline only in numeric tokens
        len(NUMTOK_RE.findall(l)) >= 1 and not re.search(r"\b(def|class|import|for|while|if|return)\b", l)
        for l in train_diff["added"]) if train_diff["added"] else False
    return numeric_only and train_diff["n_changed"] <= 4


def candidate_ledger(mission, base_train, base_kern):
    exp = Path(mission) / "experiments"
    rows = []
    for d in sorted(exp.glob("candidate_*")):
        score = None
        sr = d / "SCORE.raw"
        rr = list(d.glob("RESULT.md"))
        text = ""
        if sr.exists():
            text += sr.read_text(errors="replace")
        for r in rr:
            text += "\n" + r.read_text(errors="replace")
        m = SCORE_RE.search(text)
        ct = d / "train.py"
        ck = d / "triton_kernels.py"
        td = changed_lines(base_train, ct) if (base_train and ct.exists()) else None
        kd = changed_lines(base_kern, ck) if (base_kern and ck.exists()) else None
        if kd is not None:
            kernels_touched = kd["n_changed"] > 0
        else:
            # No kernel snapshot: fall back to a recorded diff/patch, else UNKNOWN
            # (None) — never silently report False, which misleads the Meta-Critic.
            diff_txt = ""
            for pat in ("recipe.diff", "diff_vs_record83.patch"):
                pf = d / pat
                if pf.exists():
                    diff_txt += pf.read_text(errors="replace")
            kernels_touched = ("triton_kernels.py" in diff_txt) if diff_txt else None
        rows.append({
            "name": d.name,
            "valid": (m.group(1) == "true") if m else None,
            "val_loss": float(m.group(3)) if m else None,
            "p": float(m.group(4)) if m else None,
            "train_time_s": float(m.group(5)) if m else None,
            "train_lines_changed": td["n_changed"] if td else None,
            "kernels_touched": kernels_touched,
            "single_knob": is_single_knob(td, kernels_touched),
        })
    return rows


def mission_outcomes(life_dir):
    al = Path(life_dir) / "activity.log"
    if not al.exists():
        return []
    out = []
    for line in al.read_text(errors="replace").splitlines():
        if "MISSION  start" in line or "MISSION  done" in line or "verdict=" in line:
            out.append(line.strip()[:200])
    return out[-40:]


def main():
    mission = sys.argv[1]
    life = sys.argv[2]
    base_train = sys.argv[3] if len(sys.argv) > 3 else str(Path(mission) / "baseline/record83_train.py")
    base_kern = sys.argv[4] if len(sys.argv) > 4 else str(Path(mission) / "baseline/record83_triton_kernels.py")

    led = candidate_ledger(mission, base_train, base_kern)
    valids = [r for r in led if r.get("valid")]
    known_k = [r for r in led if r["kernels_touched"] is not None]
    feat = {
        "n_candidates": len(led),
        "n_valid": len(valids),
        "best_valid_train_time_s": min([r["train_time_s"] for r in valids], default=None),
        "frac_single_knob": round(sum(bool(r["single_knob"]) for r in led) / max(len(led), 1), 3),
        "n_kernels_touched": sum(1 for r in known_k if r["kernels_touched"]),
        "n_kernel_unknown": sum(1 for r in led if r["kernels_touched"] is None),
        "frac_kernels_touched_of_known": round(sum(1 for r in known_k if r["kernels_touched"]) / max(len(known_k), 1), 3),
        "n_faster_but_invalid": sum(1 for r in led if r.get("valid") is False and r.get("train_time_s") and r["train_time_s"] < 80.18),
    }
    print(json.dumps({
        "features": feat,
        "candidate_ledger": led,
        "recent_mission_outcomes": mission_outcomes(life),
    }, indent=2))


if __name__ == "__main__":
    main()
