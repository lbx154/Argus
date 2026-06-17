#!/usr/bin/env python3
# Operator-held VERIFIER SEAL — argus self-improving build step 1.
# Lives OUTSIDE the agent's mission cwd (~/.argus-seal). Fail-closed: on ANY
# integrity failure it returns non-zero and the scorer must emit valid=false,
# NEVER valid=true. Closes three honesty holes the design found in eval_solution.sh:
#   (1) agent-writable analyze_sweep.py  -> hash-pin it
#   (2) self-reported train_time          -> bound it by an INDEPENDENT wall clock
#   (3) tampering the eval/timer/dataload -> require the FROZEN anchors verbatim
# Honest limit: the agent has root on the GPU box, so a DETERMINED agent could
# still defeat this; full capability-isolation needs a separate trust domain
# (the next infra step). This closes the casual/accidental holes and is the
# foundation the meta-loop must rest on.
import hashlib, re, sys
from pathlib import Path

SEAL_DIR = Path(__file__).resolve().parent

# Frozen anchors: the agent may edit the RECIPE freely, but these exact strings
# (how val is loaded, how much val is scored, how time is counted, how val is
# aggregated, and the exact scored output line) must survive verbatim.
ANCHORS = [
    'data/fineweb10B/fineweb_val_*.bin',
    'val_tokens: int = 10485760',
    'training_time_ms += 1000 * (time.perf_counter() - t0)',
    'dist.reduce(val_loss, 0, op=dist.ReduceOp.AVG)',
    'print0(f"step:{step}/{train_steps} val_loss:{val_loss:.4f} train_time:{training_time_ms:.0f}ms step_avg:{training_time_ms/max(step, 1):.2f}ms", console=True)',
]
TRAIN_TIME_FLOOR_MS = 20000  # <20s for this model/steps on 8xH100 is physically implausible
STEP_RE = re.compile(r"step:(\d+)/(\d+)\s+val_loss:[0-9.]+\s+train_time:(\d+)ms")
WALL_RE = re.compile(r"WALL_MS:(\d+)")


def fail(msg):
    print(f"SEAL FAIL: {msg}")
    sys.exit(1)


def check_manifest(name, path):
    want = None
    for line in (SEAL_DIR / "manifest.txt").read_text().splitlines():
        h, n = line.split()
        if n == name:
            want = h
    if want is None:
        fail(f"no manifest entry for {name}")
    got = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if got != want:
        fail(f"{name} hash mismatch (tampered scorer): got {got[:12]} want {want[:12]}")


def main():
    if len(sys.argv) != 4:
        fail("usage: seal_check.py <candidate_train.py> <analyze_sweep.py> <run_logs_dir>")
    train_py, analyze_py, logs_dir = sys.argv[1], sys.argv[2], Path(sys.argv[3])

    # (1) scorer integrity
    check_manifest("analyze_sweep.py", analyze_py)

    # (3) frozen anchors verbatim in the candidate recipe
    src = Path(train_py).read_text()
    missing = [a for a in ANCHORS if a not in src]
    if missing:
        fail(f"candidate tampered {len(missing)} frozen anchor(s): {missing[0][:60]!r}")

    # (2) independent wall-clock bound on the self-reported train_time
    runs = sorted(logs_dir.glob("run_*.txt"))
    if not runs:
        fail("no run logs to verify")
    checked = 0
    for f in runs:
        txt = f.read_text(errors="replace")
        m = STEP_RE.findall(txt)
        if not m:
            continue  # crashed run; analyze_sweep already drops it
        tt = int(m[-1][2])
        w = WALL_RE.search(txt)
        if not w:
            fail(f"{f.name}: no independent WALL_MS recorded (cannot trust self-reported train_time)")
        wall = int(w.group(1))
        if tt < TRAIN_TIME_FLOOR_MS:
            fail(f"{f.name}: train_time {tt}ms below physical floor {TRAIN_TIME_FLOOR_MS}ms (faked timer)")
        if tt >= wall:
            fail(f"{f.name}: self-reported train_time {tt}ms >= independent wall {wall}ms (timer can't exceed wall)")
        checked += 1
    if checked == 0:
        fail("no valid run produced a parseable timed line")
    print(f"SEAL OK: anchors intact, scorer hash pinned, {checked} run(s) within [floor, wall]")
    sys.exit(0)


if __name__ == "__main__":
    main()
