---
name: Official SOL-ExecBench Environment
description: How to evaluate GPU kernels inside NVIDIA's OFFICIAL sol-execbench docker image for bit-exact, like-for-like results — build the official image, lock GPU clocks (the nested-container privilege trap), run the official harness, and understand what the local env can and cannot tell you (official latency vs the website-only SOL score).
category: benchmark-kernel-infrastructure
priority: high
version: 1
created_at: 2026-06-27T00:00:00+00:00
---

# Official SOL-ExecBench Environment

## When to use

Use this when a task wants **official, like-for-like** SOL-ExecBench numbers —
anything mentioning `sol-execbench`, NVIDIA's kernel leaderboard, an "official
image / official environment / 对标官方", or when a self-hosted scorer's number
needs to be cross-checked against the real toolchain. Pair with
`SOL Kernel SOTA Optimization` (mechanism search) and `B200 KernelBench Runtime`
(the eval-server contract). This skill owns **environment parity**, not the
kernel idea.

Core principle (project value #1 — *the agent is smarter than the harness*):
the harness is a dumb, faithful measurement pipe. Environment parity is what
makes the measurement trustworthy. **The eval must run in the exact reference
environment; the agent stays outside it.**

## Why the official environment matters

A kernel's SOL score depends on one measured number `t_k` (your latency). `t_k`
drifts with the **software toolchain** (CUDA/nvcc/driver, torch/triton/cutlass
versions), **GPU clocks**, and the **timing protocol**. A self-hosted
approximation can be optimistic or pessimistic by 10–30% (especially for
`.cu`/CUTLASS kernels, whose compilation is toolchain-sensitive). The official
docker image pins all of it, so your local `t_k` matches what NVIDIA measures
server-side as closely as physically possible (the only residual is "your B200
is a different physical card").

## Get the official image + data (one-time, on the B200 pod)

The official repo ships the Dockerfile and the harness — there is **no
pull-able image**; you build it:

```bash
git clone --depth 1 https://github.com/NVIDIA/SOL-ExecBench    # base: nvidia/cuda:13.1.1-cudnn-devel + CUTLASS 4.4.1 + uv-pinned torch/triton
cd SOL-ExecBench
# data (benchmark defs/workloads + flashinfer-trace). Needs `datasets` + `hf`:
pip install --break-system-packages datasets 'huggingface_hub[cli]'
bash scripts/download_data.sh                                   # -> data/benchmark/{L1,L2,Quant,FlashInfer-Bench}, data/flashinfer-trace
# build (classic builder; the Dockerfile uses BuildKit `--mount=type=cache` —
# either `DOCKER_BUILDKIT=1`, or strip the cache mounts and build classic):
docker build --ulimit nofile=1048576:1048576 -f docker/Dockerfile \
  --build-arg HOST_UID=0 --build-arg HOST_GID=0 -t sol-execbench:official .
```

Two traps seen building docker **inside a k8s pod**:
- **overlay-on-overlay** → `docker run` fails with `invalid argument` on mount.
  Fix: set `"storage-driver": "vfs"` in `/etc/docker/daemon.json` (slower, more
  disk; needs ~20 GB for the image — fine on a fat pod).
- **`uv sync` → "No file descriptors available (os error 24)"**. The default
  soft `nofile` (1024) is too low for uv's parallel bytecode compile. Fix:
  `docker build --ulimit nofile=1048576:1048576 …`.

## ⚠ Lock the GPU clocks — the nested-container privilege trap

The official harness locks GPU/DRAM clocks for reproducible timing
(`nvidia-smi --lock-gpu-clocks`). **A privileged pod is NOT enough.** Privilege
does not propagate from the pod into a `docker run` child, and clock-locking is
gated at the **driver/cgroup layer** — `sudo`/root inside the container does not
help. Symptom:

```
Failed to lock GPU clocks: ... returned non-zero exit status 4
WARNING: Clock locking failed — proceeding unlocked        # ← jittery timings, NOT faithful
```

Fix: give the **nested** container the privilege too —
`docker run --privileged …` (or `--cap-add=SYS_ADMIN`). Verify:

```bash
docker run --rm --privileged --gpus all --entrypoint /bin/bash sol-execbench:official \
  -c 'nvidia-smi --lock-gpu-clocks=1500; echo exit=$?'      # exit=0 ⇒ locked. exit=4 ⇒ still unlocked.
```

If you can never lock clocks (e.g. a node policy forbids it), say so explicitly
and treat the latency as "unlocked, ±jitter" — do not present it as the faithful
official number.

## Run the official harness

`run_dataset.py` is the official runner; it auto-wraps a `.py` (with top-level
`def run(...)`) or loads a `.json` solution, matching the kernel's
`definition.json`. The entrypoint requires the trace dir mounted + an env var:

```bash
docker run --rm --privileged --gpus '"device=0"' -e CUDA_VISIBLE_DEVICES=0 \
  -v $REPO:/sol-execbench -w /sol-execbench \
  -e FLASHINFER_TRACE_DIR=/sol-execbench/data/flashinfer-trace \
  sol-execbench:official \
  python scripts/run_dataset.py data/benchmark/L1/<kernel> \
    --solution-name solution.py -o /sol-execbench/results/<kernel>
# results/<kernel>/**/summary.json -> {passed, failed, latencies_ms[]}
# correct = (failed==0); your t_k = geomean(latencies_ms)
```

For a long campaign, stand the image up as an **eval server, one per GPU**
(ports 9100-9107), `--privileged` each, and POST candidates — the agent never
enters the container. Keep the `RESULT … correct=… cand_ms=… SOL=…` line format
so the library recognizes wins (`event_log.py`, reviewer trust-mode).

## What the local official env CAN and CANNOT give you

| You want | Local official docker | How |
|---|---|---|
| Official **latency** `t_k` (cold-L2, locked clocks, pinned toolchain) | ✅ exact | `run_dataset.py` → `latencies_ms` |
| **correctness** under the official tolerance | ✅ exact | `summary.failed==0` |
| **speedup vs the reference** | ✅ | `t_k(yours)` vs `t_k(reference)` |
| Official **SOL score** | ❌ **not local** | needs the precomputed `t_b`/`t_sol` tables, which are **NVIDIA server-side only** (not in the public dataset; `sol_score()` exists in the repo but is never fed real anchors). |

So: **never present a locally-computed SOL as "the official SOL".** Locally you
report *official latency + speedup vs reference*. For the true SOL score, submit
the `.py`/`.json` to the website (first 5/day are free, no delay).

## Official timing protocol (so you optimize the right thing)

- 10 warmup + 50 timed iterations × 3 trials; reported runtime = mean across
  trials. **Cold L2**: the L2 cache is flushed before every timed iteration —
  so kernels that "win" by warming a cache will not win here. Inputs are cloned
  per iteration (in-place tricks don't carry over). Fixed clocks.
- **Anti-cheat is enforced and rejects** (→ SOL 0): monkey-patching timing,
  hidden CUDA streams outside the measured path, background threads during eval,
  lazy-proxy outputs instead of concrete tensors, degenerate (NaN/inf/all-zero)
  outputs. Do not chase any of these — they are detected and they are dishonest.

## Verified facts (re-confirm, don't trust blindly)

- Image base: `nvidia/cuda:13.1.1-cudnn-devel-ubuntu24.04` + CUTLASS `v4.4.1` +
  uv-pinned torch/triton (`docker/Dockerfile`).
- An official-format solution is `{name, definition, author, spec:{languages,
  target_hardware, entry_point, sources:[{path,content}]}}`; a single `.py` with
  `def run(...)` is auto-wrapped by `run_dataset.py`.
- Measured on this setup (locked clocks): `053_gaussian_topk_sparse_activation`
  reference = ~0.97 ms; an optimized triton solution = ~0.021 ms (≈46× faster),
  correct under the official tolerance. A self-hosted scorer had reported
  ~0.0186 ms for the same solution — same order, confirming honesty.
