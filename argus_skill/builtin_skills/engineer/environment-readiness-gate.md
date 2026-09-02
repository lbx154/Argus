---
name: "Environment Readiness Gate"
description: "Verify the project environment, public data/evaluator, dependencies, storage, and only the compute/API resources an experiment actually uses before producing evidence."
---

# Environment Readiness Gate

## Purpose

Prevent invalid or wasted runs without assuming every AI research project uses
CUDA, Hugging Face models, an LLM API, or a training framework.

Run this gate before the first real benchmark/evidence call and before each
substantively different pilot, full, or ablation launch.

## Applicability rule

Verify only resources the experiment actually uses. Mark irrelevant sections
`NOT_APPLICABLE` with one sentence; do not fabricate a CUDA, model-weight, or API
dependency to satisfy the checklist.

## Required checks

### 1. Project environment

- Record the interpreter/runtime/compiler executable and version.
- Confirm dependencies import or execute from the project environment rather
  than the Argus framework environment.
- For Python projects with a venv, prefer `./.venv/bin/python` on POSIX or
  `.\.venv\Scripts\python.exe` on Windows.
- Record package-lock and configuration versions when they are claim-relevant.

POSIX-shell example:

```bash
pwd
command -v python || true
python -V || true
test -x .venv/bin/python && .venv/bin/python -V || true
```

Windows PowerShell example (compatible with Windows PowerShell 5.1):

```powershell
Get-Location
Get-Command python -ErrorAction SilentlyContinue
python -V
if (Test-Path -LiteralPath '.\.venv\Scripts\python.exe') {
  & '.\.venv\Scripts\python.exe' -V
}
```

### 2. Public evidence source

- Verify the public benchmark/dataset/task suite or official evaluation release
  can be retrieved or is present locally.
- Record official URL/repository, version/commit, split/cohort, license/access
  condition, checksum when practical, and any filtering/conversion script.
- Confirm synthetic/generated diagnostics are labeled separately from public
  evidence.

### 3. Evaluator or analysis path

- Execute the official evaluator, metric implementation, statistical analysis,
  theorem checker, profiler, simulator, or domain-native verifier on a tiny
  known input.
- Confirm outputs are non-empty and semantically plausible.
- For custom evaluators, compare against an official/reference implementation
  on at least one shared example.

### 4. Compute backend

Choose the applicable branch:

**CPU / compiler / systems**

- Record CPU/runtime/compiler/OS details relevant to the measurement.
- Verify required binaries, permissions, clocks/affinity policy, and timing
  method where applicable.

**GPU / accelerator**

- Confirm the allocated devices match the operator/runtime allocation.
- Verify the framework sees the expected devices and has enough memory for the
  planned smoke run.
- Record driver/runtime/framework versions when they affect correctness or
  performance.

Example for a GPU experiment:

```bash
nvidia-smi --query-gpu=index,name,memory.free --format=csv,noheader
./.venv/bin/python - <<'PY'
import torch
print(torch.__version__, torch.cuda.is_available(), torch.cuda.device_count())
PY
```

On Windows PowerShell, use the project interpreter and `-c` instead of a
POSIX heredoc:

```powershell
nvidia-smi --query-gpu=index,name,memory.free --format=csv,noheader
& '.\.venv\Scripts\python.exe' -c 'import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.device_count())'
```

GPU checks are `NOT_APPLICABLE` for CPU-only, API-only, theoretical, or
non-accelerator work.

### 5. Models and frameworks

- Import only the frameworks selected by the plan.
- Verify required checkpoints/assets are present and match the declared
  revision.
- HF/Torch cache checks apply only when the run uses those ecosystems.
- A custom runtime/trainer/evaluator is allowed when required by the research;
  verify it against a trusted reference rather than rejecting it by category.

### 6. External APIs

- Test only routes the experiment will call.
- Confirm one minimal non-empty response without printing credentials, private
  endpoints, or raw capability-vault contents.
- API checks are `NOT_APPLICABLE` when the experiment is fully local.

### 7. Storage and outputs

- Confirm enough disk space for expected outputs/checkpoints.
- Create the run directory and verify it is writable.

### 8. Cancellation and observability

For long-running work:

- verify status/progress/log paths;
- verify the cancellation mechanism or scheduler stop path;
- confirm the worker reports an initial heartbeat.

Short deterministic commands may simply capture stdout/stderr.

Report only a concrete blocker and the decisive command or output through the
normal Engineer response. Do not create a separate preflight file.

## Reviewer hook

The Reviewer should keep the stage open when an applicable readiness check is
missing, stale, contradictory, or failed. The Reviewer must not require CUDA,
HF caches, base-model weights, or API calls for an experiment that does not use
them.
