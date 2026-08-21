---
name: "NanoGPT Speedrun H100 SOTA"
description: "Playbook for Recursive Task 2 / NanoGPT speedrun on 8×H100: use the frozen scorer, preserve environment parity, maintain a certified global-best floor, run basin-hopping/co-tuning experiments, and document real verifier results toward SOTA."
---

## Title
NanoGPT Speedrun H100 SOTA

## Description
Optimize `solution/train.py` and `solution/triton_kernels.py` for Recursive "First Steps" Task 2: minimize certified wall-clock time to FineWeb `val_loss <= 3.28` on 8×H100, using the frozen scorer and t-test gate.

## When to use
- The task mentions NanoGPT Speedrun, Recursive Task 2, 8×H100, FineWeb, `val_loss <= 3.28`, `eval_solution.sh solution N`, record #83, or train-time SOTA.
- The project root contains `TASK.md`, `AGENTS.md`, `solution/train.py`, `solution/triton_kernels.py`, and the frozen scorer.
- The goal is a benchmark result, not a paper; research still matters but must serve faster valid training.

## When NOT to use
- The task is KernelBench/SOL GPU kernel optimization; use `SOL Kernel SOTA Optimization` or `SOL Kernel Hands-on Trace`.
- The task is NanoChat/BPB pretraining; follow that project's frozen harness and NanoChat vertical instead.
- The frozen Task 2 environment is missing and cannot be restored; write a blocker report instead of changing comparability.

## Non-negotiable contract
- Only edit `solution/train.py` and `solution/triton_kernels.py`.
- The frozen score is only:

```bash
./eval_solution.sh solution 3      # iterate
./eval_solution.sh solution 10     # certify a new best
```

- Validity requires `p(mean val_loss < 3.28) < 0.01`.
- Score is mean `train_time` among valid candidates.
- Use the remote command and frozen scoring interpreter declared by the mission
  manifest; do not assume a host alias or filesystem path.
- Do not alter val data, val loss, target, t-test, scorer, FA3 environment, or timing protocol.
- Do not search for leaked "best"/"optimized"/answer recipes. Discover your own speedups.

## Current known project facts (update if stale)
These facts come from the existing `nanogpt-speedrun-h100` trace and should be rechecked before acting:

- Starting point is leaderboard record #83 ("Sign Trick on Bigram Embed"), near 80s on 8×H100.
- Known external frontier is about `77.3s`; beating this is the real SOTA target.
- A certified N=10 run exists:

```text
runs n=10 val_loss=3.2776±0.0022 p(mean<3.28)=0.004007 time=79.77±0.06s
```

- Best N=3 valid floor seen in local experiments:

```text
candidate_delayed_embed_split_192: valid=true n=3 val_loss=3.2765 p=0.007355 time=80.16s
```

- Faster but invalid active-line points existed:

```text
full post-only MLP:        valid=false p=0.0847  time=79.55s
hybrid exact layers 8..10: valid=false p=0.01313 time=79.73s
hybrid exact layers 7..10: valid=false p=0.02709 time=79.86s
```

- A factor-only MLP mechanism recovered validity but was slower:

```text
factor-only 0..7: valid=true p=0.004671 time=81.39s
```

Use these as **priors**, not as eternal truth. Re-open `experiments/*/RESULT.md`, raw logs, and the current `solution/` before choosing the next line.

## Operating loop

### 1. Re-establish ground truth
Before launching a new candidate:

1. Read `TASK.md`, `AGENTS.md`, `research/GROUND_TRUTH.md`, `research/PROFILE.md`, `research/TECHNIQUE_NOTES.md`, and `experiments/OPTIMIZATION_LEDGER.md`.
2. Initialize the runtime values from the mission manifest:

```bash
export NANOGPT_REMOTE='<remote from mission manifest>'
export NANOGPT_BENCH_ROOT='<benchmark root from mission manifest>'
export NANOGPT_PYTHON='<frozen Python interpreter from mission manifest>'
test -n "$NANOGPT_REMOTE" -a -n "$NANOGPT_BENCH_ROOT" -a -n "$NANOGPT_PYTHON"
```

3. Check whether a run is already active:

```bash
ssh "$NANOGPT_REMOTE" 'ps -eo pid,etime,cmd | grep -E "torchrun|train.py|run_sweep|eval_solution" | grep -v grep || true'
```

4. If a prior run is complete but uncollected, collect it first. Do not start another run over uncollected evidence.
5. Confirm current `solution/` hashes and identify which previous candidate it corresponds to.

### 2. Maintain two lines of state
- **GLOBAL BEST/FLOOR**: lowest valid scorer-certified time. Never overwrite it with an invalid or merely faster run.
- **ACTIVE LINE**: a structural idea that may be temporarily invalid or slower while it matures. Give it 2-4 co-tuning rounds before killing it.

Record both in `experiments/OPTIMIZATION_LEDGER.md` after every run.

### 3. Choose high-leverage mechanisms
Do not spend the night on tiny LR/WD nibbles unless they support a structural mechanism. Prefer:

- Step-budget reductions only when paired with quality recovery.
- MLP activation storage/backward recomputation variants, but track their exact validity/time tradeoff.
- Attention/kernel changes only if FA3 parity is preserved; never silently fall back to FlexAttention or SDPA.
- Schedule changes that explicitly target the final t-test margin, not only final single-run loss.
- Data/packing/order changes only if the frozen held-out val path remains untouched.

### 4. Run candidates with trace discipline
For every candidate directory `experiments/candidate_<name>_<timestamp>/`, write:

- `MANIFEST.md`: mechanism, parent floor, exact diff summary, command, expected risk.
- `sha256.txt` and candidate file snapshots.
- `eval_solution.stdout`, `eval_solution.stderr`, `eval_solution.rc`, `SCORE.raw`.
- `logs/run_*.txt`.
- `metric_curves.txt` from `step:* val_loss:* train_time:*` lines.
- `health_grep.txt` scanning for OOM, traceback, runtime/import errors, NaN/Inf, FA3/flash errors.
- `RESULT.md`: verdict, score, curve interpretation, whether to keep/revert.

### 5. Interpret results correctly
- `valid=false` and faster is **not** a win; it is an active-line data point.
- `valid=true` and slower than floor is a useful negative result, not a replacement.
- A run with clean curves but `p` just above 0.01 may justify one co-tuning step.
- A run with runtime regression and worse p-value should be reverted or basin-hopped away from.
- N=3 is for iteration; N≥10 certifies a new best.

## Real examples from this project

### Delayed embed split — valid but near floor

```text
candidate_delayed_embed_split_192:
valid=true n=3 val_loss=3.2765 p=0.007355 time=80.16s
```

Lesson: this is a safe global floor, but it is not SOTA. Use it as a fallback while exploring structural lines.

### Post-only MLP line — faster but statistically invalid

```text
full post-only:       valid=false val_loss=3.2771 p=0.0847  time=79.55s
hybrid exact 8..10:   valid=false val_loss=3.2765 p=0.01313 time=79.73s
hybrid exact 7..10:   valid=false val_loss=3.2764 p=0.02709 time=79.86s
```

Lesson: the line is close to validity and faster than the floor, so it deserves co-tuning, but exact-layer widening did not monotonically fix p-value. Do not blindly add exact layers.

### Factor-only MLP — validity recovered but speed lost

```text
factor-only 0..7: valid=true n=3 val_loss=3.2767 p=0.004671 time=81.39s
```

Lesson: reconstructing `post = factor * factor` in backward can recover correctness/validity but may add compute/memory traffic and lose the speed advantage. If revisiting this family, fuse the reconstruction into the projection path or choose a different structural MLP basin.

### N=10 certification

```text
n=10 val_loss=3.2776±0.0022 p=0.004007 train_time=79.77±0.06s
```

Lesson: this breaks the local 80.16s N=3 floor and is near the public record, but still does not beat the 77.3s frontier. Treat it as the current certified baseline unless a fresher valid N≥10 result exists.

## H100 connection instructions
- Read `$NANOGPT_REMOTE`, `$NANOGPT_BENCH_ROOT`, and `$NANOGPT_PYTHON`
  from the mission manifest. Verify the exact stack instead of assuming access:

```bash
ssh "$NANOGPT_REMOTE" "$NANOGPT_PYTHON -c \
  \"import torch, triton; print(torch.__version__); \
  print(torch.cuda.is_available(), torch.cuda.device_count()); \
  print(triton.__version__)\""
```

- Check GPU saturation during runs:

```bash
ssh "$NANOGPT_REMOTE" 'nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total,power.draw --format=csv,noheader,nounits'
```

All eight GPUs should be busy during `torchrun --nproc_per_node=8`.

## Overnight behavior
If the agent is running unattended:

1. Do not launch parallel scorers; each scorer uses all 8 GPUs.
2. Always collect completed runs before launching the next one.
3. If no candidate beats the certified floor after several attempts, preserve the negative evidence and pivot to a different mechanism class.
4. Generate/update an HTML report in the project root summarizing:
   - current global best,
   - active line,
   - every candidate,
   - valid/invalid,
   - train_time,
   - p-value,
   - mechanism lesson,
   - whether any skill was generated or revised.

## Response shape
- Quote the exact `SCORE` line.
- State whether the candidate is valid.
- State whether it replaces the global best.
- Link `RESULT.md`, raw logs, and diffs.
- Name the next mechanism or pivot; do not say "try more tuning" without a mechanism.
