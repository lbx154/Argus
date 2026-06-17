---
name: SOL Kernel Hands-on Trace
description: A concrete, failure-first KernelBench/SOL trace from optimizing 36_RMSNorm_ on B200, documenting scorer downtime, candidate-shape mistakes, gpu_run file-sync traps, debug commands, and final verified SOL improvement.
category: benchmark-kernel-optimization
version: 1
scientist_model: gpt-5.5
created_at: 2026-06-17T00:00:00+00:00
---

## Title
SOL Kernel Hands-on Trace

## Description
Use this skill when the agent is about to optimize a KernelBench/SOL-style kernel and needs a **real execution trace**, including mistakes and recovery steps, rather than a polished abstract playbook.

## When to use
- The task is a correctness-gated GPU kernel benchmark with a frozen scorer.
- The agent must produce real speed/SOL numbers on B200/H100/A100 and may need to debug compile/correctness/timing failures.
- The user asks for a trace, postmortem, or “what actually goes wrong when you try this yourself”.
- A benchmark vertical task is stuck because the agent is treating the scorer/GPU bridge as a black box.

## When NOT to use
- You only need the high-level SOL workflow. Use `SOL Kernel SOTA Optimization`.
- The task is a paper benchmark matrix or model-training run rather than one kernel implementation.
- The scorer is unavailable and the user explicitly asks you not to attempt recovery.

## Real trace: KernelBench 36_RMSNorm_ on B200

This trace was run in `/home/argustest/kb-hands-on-trace`, copied from the real `kernelbench-mission-b200` scaffold. The target was `solutions/36_RMSNorm_.py`, with official scoring by:

```bash
./eval_solution.sh solutions
```

The scorer talks to a frozen B200 eval server at `http://127.0.0.1:2232`; debug runs use:

```bash
python gpu_run.py <script.py>
```

### Step 0 — reset to baseline

I reset the candidate:

```bash
cp baseline/36_RMSNorm_.py solutions/36_RMSNorm_.py
```

The intended reference operation is RMSNorm along `dim=1` for shape `(112, 64, 512, 512)`:

```python
rms = torch.sqrt(torch.mean(x ** 2, dim=1, keepdim=True) + eps)
return x / rms
```

### Nail 1 — scorer bridge down

First official scorer attempt failed:

```text
ERROR: B200 eval server unreachable at http://127.0.0.1:2232 ([Errno 111] Connection refused)
```

Lessons:
- Do not fabricate or infer a score when the scorer is down.
- Use `set -o pipefail` when piping scorer output through `tee`; otherwise a scorer failure can look successful.
- Check `B200_SETUP.md` before guessing. In this environment, `127.0.0.1:2232` is a `kubectl port-forward` to `argus-kbench-evalsrv:9000`.
- Recovery was:

```bash
kubectl port-forward pod/argus-kbench-evalsrv 2232:9000 --address 127.0.0.1
curl -fsS http://127.0.0.1:2232/health
```

### Nail 2 — copied baseline was not a valid candidate

After the bridge was restored, the official scorer said:

```text
36_RMSNorm_ N 0.000 ... INCORRECT ✗
└─ ERROR: candidate defines no ModelNew
```

Cause:
- `baseline/36_RMSNorm_.py` contains `Model`, `get_inputs`, and `get_init_inputs`.
- Candidate files under `solutions/` must define `ModelNew`.

Fix:

```python
class ModelNew(Model):
    pass
```

### Nail 3 — fresh sandbox missed `results/`

The scorer printed a `RESULT` line, then crashed while appending history:

```text
FileNotFoundError: [Errno 2] No such file or directory: 'results/sol_history.csv'
```

Cause:
- The full mission checkout already had `results/`.
- A freshly copied sandbox did not.

Fix:

```bash
mkdir -p results
```

Trace rule:
- Record both the visible `RESULT` line and the exit code. A printed score followed by a non-zero exit is still a failed run until the artifact problem is fixed.

### Baseline after fixing candidate shape and results dir

Command:

```bash
set -o pipefail
./eval_solution.sh solutions | tee attempts/36_rmsnorm_hand_v1/baseline_eval_fixed.log
```

Official result:

```text
36_RMSNorm_ Y 8.898ms 0.99x SOL=0.498 tc_SOL=0.619 opt_ms=1.879 loses to tc
RESULT mean_SOL=0.5536 correct=8/8 beats_torch.compile=4/8
Most headroom now: 40_LayerNorm, 36_RMSNorm_, 23_Softmax.
```

Decision:
- `36_RMSNorm_` is still worth attacking: it is correct, slow, loses to torch.compile, and has a clear memory-bound eager-pass bottleneck.

### Nail 4 — wrong reduction axis

My first actual code attempt was intentionally simple and wrong:

```python
class ModelNew(Model):
    def forward(self, x):
        # WRONG: normalizes the last dimension, not dim=1.
        rms = torch.sqrt(torch.mean(x ** 2, dim=-1, keepdim=True) + self.eps)
        return x / rms
```

Then I tried to debug it on B200 using:

```python
spec_from_file_location("candidate36", "solutions/36_RMSNorm_.py")
```

That failed remotely:

```text
FileNotFoundError: /workspace/scratch/solutions/36_RMSNorm_.py
```

Cause:
- `gpu_run.py` sends only the debug script body to the B200 `/run` endpoint.
- It does **not** sync local project files.

Fix:
- Embed the candidate source inside the debug script, or use the official scorer endpoint.

Embedded-source debug then exposed the real math bug:

```text
shape (112, 64, 512, 512)
max_abs_err 0.8273553848266602
mean_abs_err 0.04104115068912506
allclose_1e-2 False
```

Lessons:
- Always verify the reduction axis before optimizing speed.
- Debug scripts must be self-contained when using `gpu_run.py`.
- A wrong-axis implementation can look plausible from code structure but is completely invalid numerically.

### Fix — fused CUDA candidate

Mechanism:
- Keep the reference semantics: reduce over feature dimension `C=64`.
- One CUDA thread owns one `(batch, spatial)` position.
- The thread loops over 64 channels in registers, accumulates `sum_sq`, computes `rsqrt(sum_sq / 64 + eps)`, then writes 64 normalized values.
- Use `load_inline` for a cached CUDA extension.

Implementation skeleton:

```python
from torch.utils.cpp_extension import load_inline

class ModelNew(Model):
    def forward(self, x):
        return _get_rmsnorm_ext().rmsnorm_b200_forward(x, float(self.eps))
```

CUDA kernel skeleton:

```c
const long base = b * 64L * spatial + s;
float vals[64];
float sum_sq = 0.0f;

#pragma unroll
for (int c = 0; c < 64; ++c) {
    const float v = x[base + (long)c * spatial];
    vals[c] = v;
    sum_sq += v * v;
}

const float inv_rms = rsqrtf(sum_sq * 0.015625f + eps);

#pragma unroll
for (int c = 0; c < 64; ++c) {
    y[base + (long)c * spatial] = vals[c] * inv_rms;
}
```

Self-debug on B200:

```text
shape (112, 64, 512, 512)
max_abs_err 8.344650268554688e-07
mean_abs_err 5.325315299842259e-08
allclose_1e-2 True
```

### Final official scorer

Official result:

```text
36_RMSNorm_ Y 2.258ms 3.90x SOL=0.893 tc_SOL=0.619 opt_ms=1.879 BEATS tc ✓
RESULT mean_SOL=0.6026 correct=8/8 beats_torch.compile=5/8
[scorer] NEW GLOBAL BEST mean_SOL=0.6026 (prev 0.5536)
```

What improved:
- `36_RMSNorm_`: `SOL 0.498 → 0.893`
- `36_RMSNorm_`: `8.898ms → 2.258ms`
- whole run: `mean_SOL 0.5536 → 0.6026`
- torch.compile beats: `4/8 → 5/8`

## What this trace teaches

1. **The first failure may be infrastructure, not algorithm.**
   - Scorer bridge down is not a kernel failure.
   - Stop and fix observability/bridge before judging the method.

2. **A baseline file is not always a valid candidate file.**
   - Candidate surface requires `ModelNew`.
   - A benchmark skill should tell the agent to check candidate API shape before scoring.

3. **Fresh sandboxes miss stateful directories.**
   - Create `results/`, `attempts/`, and log paths before scoring.
   - Use `pipefail` so artifact write failures are visible.

4. **Debug transport matters.**
   - `gpu_run.py` does not sync local files.
   - Embed source or use the official upload endpoint.

5. **Correctness bugs are often semantic, not syntax.**
   - Wrong reduction axis is easy on normalization kernels.
   - Always print `max_abs_err`, `mean_abs_err`, and `allclose` before official timing.

6. **A good kernel target is selected by measured gap, not vibes.**
   - The scorer named headroom kernels.
   - RMSNorm was selected because it had high gap and tractable semantics.

7. **Do not stop at "beats eager".**
   - The actual bar is torch.compile and SOL target.
   - The result should report both per-kernel SOL and full mean SOL.

## How to apply this trace to another benchmark kernel

1. Run the official scorer once with `set -o pipefail`.
2. If infra fails, fix or report infra; do not optimize.
3. Check that the candidate file defines the required API (`ModelNew`, unchanged `Model/get_inputs/get_init_inputs`).
4. Create missing artifact dirs before long runs.
5. Use direct GPU debug, but make debug scripts self-contained.
6. Write one deliberately small correctness probe before timing.
7. Choose the target by `tc_SOL - SOL`, `opt_ms`, and mechanism plausibility.
8. Implement one mechanism.
9. Re-run debug correctness.
10. Run official scorer and record the exact `RESULT` line.

## Response shape
- State the exact scorer command and whether it exited 0.
- Quote every infrastructure/correctness failure before the final success.
- Quote the debug correctness output (`max_abs_err`, `allclose`).
- Quote the official final per-kernel line and `RESULT` line.
- State the measured improvement and the next headroom target.
