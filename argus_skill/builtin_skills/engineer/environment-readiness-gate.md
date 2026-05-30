---
name: Environment Readiness Gate
description: Force the engineer to verify the project environment (venv, GPU, CUDA, framework imports, model weights, API routes) BEFORE launching any benchmark or training run. Applies during the benchmark and run stages.
category: engineering
priority: high
version: 1
created_at: 2026-05-28T00:00:00+00:00
---

# Environment Readiness Gate

## Why this skill exists

The engineer keeps launching `accelerate launch ... train.py` or
`python eval_benchmark_matrix.py` without first verifying that the
environment is actually capable of running the experiment. The result
is wasted GPU-hours, half-written `status.json` files that say
`completed: 0`, and a reviewer round that has to roll back the
pipeline state machine because the run never actually ran.

This skill is the **mandatory pre-flight checklist** for every
benchmark-stage harness wire-up and every run-stage launch. Do NOT
skip it because "we did this last round" — package updates, the
`./.venv` not being activated in this shell, a stale `CUDA_VISIBLE_DEVICES`,
or a model download that failed silently can all invalidate the
previous round's environment.

## When to use

- The benchmark stage is about to mark `benchmark.smoke` as satisfied
  by actually scoring at least one real row.
- The run stage is about to launch a pilot, full, or ablation run.
- A previous run failed with `ImportError`, `CUDA out of memory`,
  `ModuleNotFoundError`, `NaN reward`, `0 tasks completed`, or
  `state: error` in the first 30 seconds.
- The reviewer rolled back the pipeline because evidence looked fake
  and you suspect the framework was never actually loaded.

## The pre-flight contract (every benchmark / run launch)

Run **every** command in this section before the first launch of any
benchmark or training run, and capture the verbatim output in
`experiments/runs/<run_id>/preflight.txt`. Do NOT proceed if any
check fails — fix the environment first.

### 1. Project venv is activated and self-consistent

```bash
which python && python -V
test "$(which python)" = "$(pwd)/.venv/bin/python" \
    && echo "OK: project venv active" \
    || { echo "FAIL: not running ./.venv/bin/python"; exit 1; }
./.venv/bin/python -c "import sys; print(sys.executable); print(sys.path[:3])"
```

If `which python` does not resolve to `<project>/.venv/bin/python`,
either `source .venv/bin/activate` or call `./.venv/bin/python`
explicitly for every command from this point on. Do NOT continue
into anything that imports torch / diffusers / transformers without
this resolved.

### 2. GPU allocation matches the operator vault

```bash
./.venv/bin/python - <<'PY'
import os, subprocess, json
vault = json.load(open(os.path.expanduser("~/.argus-skill/capabilities/gpu_resources.json")))
expected = vault.get("cuda_visible_devices", "")
actual = os.environ.get("CUDA_VISIBLE_DEVICES", "<unset>")
print(f"vault says CUDA_VISIBLE_DEVICES={expected!r}; shell has {actual!r}")
assert actual == expected, "CUDA_VISIBLE_DEVICES mismatch — fix before launching"
out = subprocess.check_output(["nvidia-smi", "--query-gpu=index,name,memory.free", "--format=csv,noheader"], text=True)
print(out)
PY
```

### 3. Framework imports actually succeed

For every chosen training/inference framework, do a real `import`
before launching:

```bash
./.venv/bin/python - <<'PY'
import importlib, sys
for module in ("torch", "diffusers", "transformers", "accelerate", "peft"):
    try:
        m = importlib.import_module(module)
        print(f"OK {module} {getattr(m, '__version__', '?')}")
    except Exception as exc:
        print(f"FAIL {module}: {exc!r}")
        sys.exit(1)
PY
```

Add the *specific* framework module from `research/INFRA_CHOICE.md`
to the list above (e.g. `simpletuner`, `verl`, `agentgym_rl`, …).
If any import fails, `pip install` the missing dep into
`./.venv/bin/pip` **only** — never into the Argus framework venv.

### 4. Torch sees the GPUs

```bash
./.venv/bin/python - <<'PY'
import torch
assert torch.cuda.is_available(), "torch reports no CUDA — driver/runtime mismatch?"
print(f"torch={torch.__version__}, cuda={torch.version.cuda}, devices={torch.cuda.device_count()}")
for i in range(torch.cuda.device_count()):
    free, total = torch.cuda.mem_get_info(i)
    print(f"  cuda:{i} {torch.cuda.get_device_name(i)} free={free/1e9:.1f}GB total={total/1e9:.1f}GB")
PY
```

### 5. HF / Torch cache points at the project model store

```bash
./.venv/bin/python - <<'PY'
import os
for var in ("HF_HOME", "HUGGINGFACE_HUB_CACHE", "HF_DATASETS_CACHE", "TRANSFORMERS_CACHE", "TORCH_HOME"):
    val = os.environ.get(var, "<unset>")
    expected_prefix = os.path.join(os.getcwd(), "models")
    ok = val.startswith(expected_prefix)
    print(f"{'OK' if ok else 'FAIL'} {var}={val}")
    assert ok, f"{var} does not start with {expected_prefix} — fix before downloading anything"
PY
```

### 6. Model weights actually present (NOT a stub directory)

```bash
ls -la models/ 2>&1
du -sh models/* 2>&1 | head
```

If the chosen base model (e.g. Z-Image, Llama, etc.) is not on disk
yet, download it explicitly through the chosen framework's download
path **before** the run. Do not let the training script discover the
missing weight at minute 30 and crash.

### 7. Model API routes actually reachable (if rewards/scoring use the API)

```bash
./.venv/bin/python - <<'PY'
import json, os
vault = json.load(open(os.path.expanduser("~/.argus-skill/capabilities/model_api.json")))
routes = vault["capabilities"]["model_api"]["routes"]
for name in ("text", "image", "image_review"):
    if name in routes:
        r = routes[name]
        print(f"OK {name}: model={r.get('model')} base_url={r.get('base_url')}")
PY
```

For each route the experiment will hit, also do one real test call
through `code/llm.py` (or the chosen framework's API client) to a
trivial prompt. Confirm a non-empty response. A 401 / 429 / 404 here
is a HARD blocker — fix it before launching anything that will burn
the daily API budget on every prompt.

### 8. Disk has room for outputs

```bash
df -h . | tail -1
```

A run that writes thousands of rasters/checkpoints and runs out of
disk at minute 40 is an avoidable failure.

### 9. Subagent / cancellation contract works

If the run will be submitted through
`python -m argus_skill.tools.subagent submit`, verify the helper
itself responds first:

```bash
./.venv/bin/python -m argus_skill.tools.subagent status --task-id smoke-check 2>&1 | head -5
```

For long runs, also confirm that the harness honors the STOP file
by writing one to a quick smoke run and watching it exit cleanly.

## Reviewer hook

The L2 reviewer must NOT mark `benchmark.smoke`, `benchmark.evaluator_authentic`,
`run.matrix`, or `run.scale` as satisfied without seeing
`experiments/runs/<run_id>/preflight.txt` quoting the verbatim output
of every command above. A reviewer round that finds:

- `which python` points at the argus-skill framework venv (`$ARGUS_SKILL_PYTHON`) instead of the project `.venv`
- `CUDA_VISIBLE_DEVICES` does not match the vault
- `torch.cuda.is_available()` is False
- HF cache env vars point outside `<project>/models/`
- model weights directory is empty when the run needs a base model
- API routes were never test-called

…must reply `continue` with a `next_action` telling the engineer to
re-run this skill end-to-end and re-attach the preflight.txt before
launching anything.

## What this skill is NOT

- Not a smoke test of the *experiment* — that's `benchmark.smoke`.
- Not a substitute for the per-stage checklist; it's the gate that
  runs *before* each benchmark/run round even starts producing
  evidence.
- Not a one-time check at project creation. Re-run every time the
  engineer is about to launch a new benchmark/training/eval
  invocation, because pip installs, repo updates, and shell sessions
  drift.
