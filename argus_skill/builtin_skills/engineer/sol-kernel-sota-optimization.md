---
name: SOL Kernel SOTA Optimization
description: Playbook for KernelBench/SOL-ExecBench-style GPU kernel optimization: research public tactics, build an honest scorer, run baseline, implement one mechanism, measure correctness and speed, and record the experiment trace.
category: benchmark-kernel-optimization
version: 1
scientist_model: gpt-5.5
created_at: 2026-06-17T00:00:00+00:00
---

## Title
SOL Kernel SOTA Optimization

## Description
Optimize one correctness-gated GPU kernel toward Speed-of-Light/SOTA performance under a frozen benchmark harness, using public technique research plus measured experiments rather than blind knob tuning.

## When to use
- The objective mentions KernelBench, SOL-ExecBench, SOL score, speed-of-light, CUDA/Triton/CUTLASS kernels, B200/H100/A100 kernel timing, or speedup over PyTorch eager.
- The task names one editable kernel file, one frozen scorer, and a numeric score/speed metric.
- The user wants a benchmark result, not a paper; research is still required, but only to identify scorer facts, SOTA tactics, and a first-score plan.

## When NOT to use
- The task is a paper benchmark matrix, multi-family agent evaluation, or EMNLP evidence run; use the research-experiment runner instead.
- The evaluator/scorer is missing and cannot be reconstructed from the task; first write a setup/blocker report instead of inventing a metric.
- The only requested output is prose analysis or a literature review with no editable kernel and no frozen scorer.

## Core principle
Do not treat a kernel benchmark as a black box. Split the work into inspectable units:

1. **Scorer facts**: what command scores, what correctness means, what hardware is used, what file may be edited.
2. **Technique research**: what public kernel tactics apply to this operator/hardware.
3. **Baseline**: a real measured score before modification.
4. **One mechanism**: a concrete implementation hypothesis, not random mutation.
5. **Verifier result**: correctness + runtime from the frozen scorer.
6. **Experiment trace**: decision summary, commands, outputs, and next hypothesis.

The user should be able to debug any failure by opening the artifacts, not by reading a hidden chain of thought.

## Required artifacts
- `research/GROUND_TRUTH.md`: frozen scorer, target kernel, editable file, hardware, correctness tolerance, baseline/current score, and measured bottleneck.
- `research/TECHNIQUE_NOTES.md`: external/public tactics searched and selected. Include URLs or repo/doc names, but summarize the idea in your own words.
- `research/FIRST_SCORE_PLAN.md`: exact command, editable file, expected JSON/table fields, and first mechanism to try.
- `attempts/<name>/`: implementation attempt, `CHANGES.md`, raw scorer log, and result JSON/CSV.
- `RESULTS.md` or `experiments/<run>/RUN_REPORT.md`: final comparison table and the best verified result.

## External research checklist
Before writing or changing the kernel, search/inspect public sources for the same **class** of problem:

- Official benchmark docs: SOL-ExecBench, KernelBench, or the project's frozen scorer docs.
- Operator/library docs: Triton tutorials, CUDA programming guide, CUTLASS examples, cuDNN docs, TileLang examples, PyTorch extension docs.
- Relevant issue/write-up patterns: "RMSNorm Triton kernel", "persistent reduction", "vectorized load", "warp reduction", "B200 bandwidth", "Blackwell FP8/BF16".
- Hardware facts: memory bandwidth, tensor-core support, vector width, occupancy limits, launch overhead.

Record only reusable tactics in `TECHNIQUE_NOTES.md`. Never search for or copy an answer to the exact benchmark item.

## Worked example: KernelBench 36_RMSNorm_ on B200

Use this as the quality bar for the trace. It is not a toy single-file demo: it
comes from a real KernelBench-on-B200 mission with a frozen scorer, SOL targets,
torch.compile comparison, and multiple kernels in the table.

### Mission facts
- Project: `kernelbench-mission-b200`.
- Frozen scorer: `./eval_solution.sh solutions`.
- Debug/profiling path: `python gpu_run.py <script.py>` runs arbitrary Python on the same B200 and prints full stdout/stderr.
- Editable file for the worked kernel: `solutions/36_RMSNorm_.py` (`ModelNew` only; keep `Model`, `get_inputs`, `get_init_inputs` intact).
- Shape from `sol_targets.json`: `(112, 64, 512, 512)`.
- Reference semantics: RMS normalization along feature dimension `dim=1`.
- Hardware: NVIDIA B200, CUDA 13.1, Triton 3.6, nvcc available.
- Score: per-kernel SOL where `0.5 = PyTorch eager`, `1.0 = analytical hardware limit`; correctness failure gives SOL 0.

### Baseline and target selection
The setup run read `MISSION.md`, `sol_targets.json`, `eval_solution.sh`, and a real scorer output, then wrote `research/GROUND_TRUTH.md`.

Initial official table showed:

```text
RESULT mean_SOL=0.4987 correct=8/8 beats_torch.compile=2/8
36_RMSNorm_: cand_ms=8.797, SOL=0.500, tc_SOL=0.619, opt_ms=1.879, loses torch.compile
40_LayerNorm: cand_ms=9.437, SOL=0.500, tc_SOL=0.637, opt_ms=0.067, loses torch.compile
38_L1Norm_: cand_ms=9.638, SOL=0.500, tc_SOL=0.651, opt_ms=2.147, loses torch.compile
39_L2Norm_: cand_ms=7.166, SOL=0.500, tc_SOL=0.612, opt_ms=2.147, loses torch.compile
```

Decision trace:
- Matmul was left alone: it is already cuBLAS-dominated and has little safe headroom.
- ReLU was not the first target: it already beat torch.compile and has small remaining gap.
- Norm/reduction kernels were prioritized because they were correctness-passing but spending multiple eager passes on memory-bound work.
- `36_RMSNorm_` became the active target after the mission already had partial wins on L1/L2/LayerNorm and RMSNorm remained the largest pass-through norm regression in the current table (`cand_ms=11.127`, `SOL=0.472` in one recheck).

### External technique research distilled into tactics
The agent should search/inspect public material for this class before editing, then write a short `research/TECHNIQUE_NOTES.md`. For this RMSNorm case, the useful general tactics are:

- RMSNorm over a small feature dimension (`C=64`) and huge spatial grid is a memory-bound channel reduction.
- The eager expression creates several full-tensor passes: square, mean over channel, sqrt/rsqrt, divide.
- A strong first mechanism is a fused CUDA kernel where one thread owns one `(batch, spatial)` position, loops over the 64 channels in registers, computes `sum_sq`, then writes the 64 normalized values.
- This trades parallelism inside the 64-channel reduction for very low launch count, coalesced spatial indexing, and no intermediate tensors.
- A more advanced follow-up, if needed, is to assign a warp/CTA to a spatial position and reduce channels cooperatively, but only after the one-thread-per-position kernel is measured.

### Implementation hypothesis
Hypothesis: for `(112, 64, 512, 512)`, each `(batch, h*w)` position has only 64 feature values, so a single thread can keep all 64 values in registers, compute the RMS, and write them back. The kernel launches `(ceil(spatial/256), batch)` blocks with 256 threads. That should remove eager's intermediate tensors and approach the memory-bound ceiling without risking a complex cooperative reduction.

Implementation sketch (do not copy blindly; adapt to the target shape):

```text
grid.x = ceil((512*512) / 256)
grid.y = batch
thread -> one spatial index s in one batch b
base = b * 64 * spatial + s
for c in 0..63:
    v = x[base + c * spatial]
    vals[c] = v
    sum_sq += v*v
inv_rms = rsqrt(sum_sq / 64 + eps)
for c in 0..63:
    y[base + c * spatial] = vals[c] * inv_rms
```

Why this was a good first attempt:
- It respects the exact PyTorch semantics (`dim=1`).
- It keeps correctness simple: all math is float32 and deterministic per element.
- It avoids dynamic Triton/JIT shape issues by using a straightforward CUDA extension via `load_inline`.
- It directly attacks the measured bottleneck: too many global memory passes.

### Experiment trace to record
Write this style of trace under `attempts/<name>/CHANGES.md` or `experiments/<run>/RUN_REPORT.md`:

````markdown
# 36_RMSNorm fused CUDA v1

## Scorer
- Official command: `./eval_solution.sh solutions`
- Debug command: `python gpu_run.py <debug_script.py>`
- Editable file: `solutions/36_RMSNorm_.py`
- Correctness threshold: frozen scorer; failures score SOL=0.

## Baseline / target
```text
Initial: mean_SOL=0.4987, correct=8/8
36_RMSNorm_: cand_ms=8.797, SOL=0.500, tc_SOL=0.619, opt_ms=1.879
Current pre-edit recheck: 36_RMSNorm_ cand_ms=11.127, SOL=0.472
```

## Technique research
- Public RMSNorm/LayerNorm kernel practice: fuse reduction and normalization; keep accumulation in fp32.
- For channel count 64, one thread can own one spatial position and loop over channels in registers.
- Expected failure modes: wrong reduction dimension, register pressure, uncoalesced channel-stride writes, nvcc/load_inline compile errors.

## Hypothesis
A fused CUDA kernel over `(batch, spatial)` with a 64-channel register loop will remove eager intermediates and beat torch.compile for this shape.

## Implementation
- Added a cached `load_inline` CUDA extension in `solutions/36_RMSNorm_.py`.
- `ModelNew.forward` calls `rmsnorm_b200_forward(x, eps)`.
- Preserved `Model`, `get_inputs`, and `get_init_inputs`.

## Official result
```text
RESULT mean_SOL=0.6045 correct=8/8 beats_torch.compile=5/8
36_RMSNorm_: cand_ms=2.250, SOL=0.895, beats torch.compile
[scorer] NEW GLOBAL BEST mean_SOL=0.6045 (prev 0.5546)
```

## Decision
Keep this kernel. It turned the largest pass-through RMSNorm regression into a high-SOL win and raised the full 8-kernel mean. Next target should be whichever remaining kernel has the largest `tc_SOL - SOL` gap, not another random RMSNorm tweak.
````

### What "highest level" means here
- You do not stop at "it is faster than eager"; you compare against torch.compile and the SOL target.
- You do not pick kernels randomly; you rank by verified gap to target and by plausibility of a fused mechanism.
- You do not hide failed experiments; compile/correctness failures become named lessons in the trace.
- You do not copy an exact answer; you research operator-family tactics and write your own implementation.
- You do not declare SOTA unless the protocol matches; otherwise say "SOTA-oriented" and report the exact harness.

## How to solve a new kernel item

1. **Read the mission and scorer first**
   - Identify editable files, frozen files, exact command, correctness tolerance, randomization, hardware, and score formula.
   - If the scorer is remote, write a project-local wrapper (`run_remote.sh`) so every future check is one command.

2. **Write `research/GROUND_TRUTH.md`**
   - Include the exact scorer command.
   - Run the baseline/current implementation once.
   - Record the raw JSON/output verbatim.
   - Name the binding constraint: memory bandwidth, compute/tensor core, launch overhead, synchronization, layout, or occupancy.

3. **Do focused public technique research**
   - Search for operator-family tactics, not the exact answer.
   - For each useful tactic, write: source, idea, why it might apply, risk, and how to test.
   - Reject at least one mediocre idea: e.g. "change block size only" when the bottleneck is launch overhead, or "use tensor cores" for an elementwise/reduction operator.

4. **Create the first-score plan**
   - Pick one mechanism with a measurable hypothesis.
   - State what file/function changes.
   - State the exact verification command.
   - State the expected result shape, e.g. `{correct, best_ms, score}`.

5. **Implement one mechanism**
   - Preserve the frozen harness and the public API.
   - Keep a fallback path for non-CUDA/non-target shapes if appropriate.
   - Prefer simple correct kernels before autotuning.
   - For Triton/CUDA, use explicit constants, avoid dynamic Python globals inside JIT, and check compile errors before timing.

6. **Run scorer and record trace**
   - Save raw stdout/stderr under `attempts/<name>/`.
   - Record the baseline and final JSON.
   - If incorrect, do not tune speed; fix correctness first.
   - If correct but slower, keep the measured lesson and pick a new mechanism instead of tiny random tweaks.

7. **Report only verified numbers**
   - A self-reported time is not a result. The frozen scorer's output is the result.
   - If the scorer randomizes inputs, never hard-code shapes/values beyond the allowed API contract.
   - If the benchmark has a leaderboard/SOTA, compare only under the same hardware/protocol or clearly label it as not comparable.

## Common pitfalls
- Writing a beautiful kernel but never proving correctness on randomized inputs.
- Treating research as a paper literature review instead of tactical SOTA research.
- Copying an exact benchmark answer from the internet; this invalidates the run.
- Tuning block sizes before identifying the bottleneck.
- Reporting a speedup from an unfrozen local script instead of the official scorer.
- Letting a remote SSH/GPU failure masquerade as a method failure.

## Response shape
- State the target kernel, scorer command, and hardware.
- Show baseline JSON and final JSON.
- Name the mechanism tried and whether it passed.
- Link the artifacts: `GROUND_TRUTH.md`, `TECHNIQUE_NOTES.md`, `FIRST_SCORE_PLAN.md`, `attempts/<name>/CHANGES.md`, raw log.
- If not done, give the next mechanism to test and the exact command to run.
