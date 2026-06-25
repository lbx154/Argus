#!/usr/bin/env python3
# DRAFT — nanochat-bpb VERIFIER SEAL (operator to finalize at ~/.argus-seal).
#
# The val_bpb analog of seal_check.py (which is nanogpt-speedrun: val_loss /
# train_time / fineweb). Same posture: lives OUTSIDE the agent's mission cwd,
# fail-CLOSED — on ANY integrity failure it exits non-zero and the scorer MUST
# emit valid=false, NEVER valid=true. It is the external integrity check the
# slim spine's FrozenJudge shells out to before trusting a measured val_bpb.
#
# Closes the same class of honesty holes audited in eval_solution.sh, specialized
# for nanochat val_bpb:
#   (1) tampered FROZEN metric/dataloader/scorer -> hash-pin lib.py / prepare.py /
#       eval_solution.sh against the operator-held manifest (the agent edits ONLY
#       train.py; the metric + val shard + bpb formula live in lib.py).
#   (2) a faked/self-computed val_bpb -> require the candidate to IMPORT and CALL
#       the frozen `evaluate_bpb` and print its result verbatim (ANCHORS below),
#       so it cannot print a number it made up or compute bpb on an easier set.
#
# HONEST LIMIT (same as seal_check.py): the agent has root on the GPU box, so a
# DETERMINED agent can still defeat this (e.g. read /data/shard_06542 directly
# into training = val leakage — the residual the audit flagged that only a clean-
# room / data-loader review closes). This closes the casual/accidental holes and
# is the foundation the meta-loop rests on.
#
# Usage:  seal_check_nanochat.py <candidate_train.py> <mission_dir>
# Operator: place the AUTHORITATIVE manifest_nanochat.txt at ~/.argus-seal (NOT in
# the mission cwd) and point SEAL_DIR there; the seed below is from the current
# frozen files (regenerate after any intentional frozen-file change).
import hashlib
import sys
from pathlib import Path

SEAL_DIR = Path(__file__).resolve().parent  # operator: relocate to ~/.argus-seal

# (1) The frozen files the agent must NOT touch (metric + dataloader + scorer).
FROZEN_FILES = ("lib.py", "prepare.py", "eval_solution.sh")

# (2) Anchors that must survive verbatim in the candidate train.py: it MUST use
# the frozen metric (import + call + print), not a number it computed itself.
ANCHORS = [
    "from lib import",                 # must import the frozen lib
    "evaluate_bpb",                    # must reference the frozen metric
    "evaluate_bpb(model, tokenizer",   # must CALL it on the model
    'print(f"val_bpb:',                # must print the frozen metric's result
]


def fail(msg):
    print(f"SEAL FAIL: {msg}")
    sys.exit(1)


def check_manifest(name, path):
    manifest = SEAL_DIR / "manifest_nanochat.txt"
    if not manifest.is_file():
        fail(f"no manifest_nanochat.txt at {SEAL_DIR} (operator must seal it)")
    want = None
    for line in manifest.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        h, n = line.split()
        if n == name:
            want = h
    if want is None:
        fail(f"no manifest entry for {name}")
    got = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if got != want:
        fail(f"{name} hash mismatch (tampered frozen file): got {got[:12]} want {want[:12]}")


def main():
    if len(sys.argv) != 3:
        fail("usage: seal_check_nanochat.py <candidate_train.py> <mission_dir>")
    train_py, mission_dir = sys.argv[1], Path(sys.argv[2])

    # (1) frozen metric/dataloader/scorer integrity
    for name in FROZEN_FILES:
        p = mission_dir / name
        if not p.is_file():
            fail(f"frozen file missing: {p}")
        check_manifest(name, p)

    # (2) the candidate must use the frozen metric verbatim
    src = Path(train_py).read_text()
    missing = [a for a in ANCHORS if a not in src]
    if missing:
        fail(f"candidate does not use the frozen metric ({len(missing)} anchor(s) "
             f"missing): {missing[0]!r}")

    print(f"SEAL OK: {len(FROZEN_FILES)} frozen file(s) hash-pinned, candidate uses "
          f"frozen evaluate_bpb")
    sys.exit(0)


if __name__ == "__main__":
    main()
