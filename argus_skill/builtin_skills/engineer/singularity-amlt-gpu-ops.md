---
name: Singularity AMLT GPU Operations
description: Operational playbook for Argus to submit, monitor, pause/resume, SSH, and tunnel Singularity/AMLT GPU jobs for H100/H200/A100/B200-style benchmark infrastructure without losing job state or fabricating readiness.
category: gpu-infrastructure
priority: high
version: 1
author_model: gpt-5.5
created_at: 2026-06-18T00:00:00+00:00
---

# Singularity AMLT GPU Operations

## When to use

Use this skill when a task mentions Singularity, AMLT/Amulet, `amlt run`,
`amlt status`, `amlt ssh`, `h100-speedrun`, `G8-H100`, H100/H200/A100
reservations, SSH tunnels, or "start another GPU box".

This skill is about **resource lifecycle and connectivity**. It does not decide
benchmark mechanisms; it makes the machine real, reachable, and auditable.

## Safety policy

- Prefer reversible operations: `amlt run`, `amlt status`, `amlt show`,
  `amlt logs view`, `amlt ssh`, `amlt pause`, `amlt resume`.
- Do not use destructive cleanup (`cancel`, `remove`, force edits) unless a
  human explicitly asks and confirms.
- Never treat a queued/preparing job as a usable machine.
- Never treat SSH reachability as benchmark readiness. The frozen scorer data,
  environment, and paths must also exist.

## Standard workflow

Work from the Singularity project directory:

```bash
cd /home/argustest/singularity
amlt status <experiment>
```

### 1. Inspect before submitting

```bash
amlt list --most-recent 20
amlt status <experiment>
amlt show <experiment> :<job>
```

Status meanings for operations:

- `queued` / `preparing`: reservation is not usable; no stable endpoint yet.
- `running`: endpoint may be available; start/verify tunnel.
- `paused`: no active endpoint; resume or submit a new job if a reservation is
  needed.
- terminal states: do not rely on old SSH aliases.

### 2. Submit using existing YAML templates

Prefer existing YAML templates under `/home/argustest/singularity`. For the
recorded H100 speedrun box:

```bash
amlt run h100-speedrun.yaml :gpu8-h100-speedrun=<job-name> h100-speedrun -y \
  --description "Restart 8x H100 80GB Standard speedrun box for NanoGPT Task2 frozen scorer recovery."
```

After submitting, verify acceptance and backend state:

```bash
sleep 180
amlt status h100-speedrun
```

If the job remains queued, keep polling at a slow cadence. Do not SSH-loop
aggressively against a non-running job.

### 3. Establish SSH/tunnel only after running

Use the project tunnel helpers when available:

```bash
# Self-healing tunnel helper pattern:
bash sing-jump-fast.sh <experiment> <ssh-alias> <local-port>

# Existing H100 convention in this workspace:
# experiment h100-speedrun, alias h100, local port 2210.
```

The helper rewrites `~/.ssh/config` for the alias after extracting the
Singularity websocket endpoint. If endpoint extraction is empty, the job is
usually paused/not-ready/queued.

Verify the alias:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=15 h100 \
  'hostname; nvidia-smi --query-gpu=index,name,memory.used,utilization.gpu --format=csv,noheader'
```

## Frozen benchmark readiness gate

For benchmark infrastructure (for example NanoGPT Task2), **running H100 is
necessary but not sufficient**. Before any scorer run, verify the frozen paths:

```bash
ssh h100 '
  test -x /scratch/nano/envs/sr210/bin/python &&
  test -d /scratch/nano/data/fineweb10B &&
  test -d /scratch/nano/argus_runs &&
  nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader
'
```

If `/scratch/nano`, the scorer Python, FineWeb, or `argus_runs` are missing,
write an `INFRA_BLOCKER.md` and do not start the frozen scorer. Submitting a
fresh H100 box can restore GPU capacity but may not restore the task data/env;
make that distinction explicit.

## Monitoring and logs

Use small, bounded reads:

```bash
amlt status <experiment>
amlt show <experiment> :<job>
amlt logs view -n 80 <experiment> :<job>
```

Avoid `amlt logs tail -f` in automation because it blocks indefinitely.

For local tunnel daemons, record:

- helper command,
- PID file,
- log file,
- SSH alias,
- local port,
- last successful endpoint.

## Pause/resume for resource hygiene

When the user says "stop" and the job is only a reservation/idling box, prefer:

```bash
amlt pause <experiment> :<job>
```

Confirm:

```bash
amlt status <experiment>
```

Use `resume` if the user asks to continue and the job is still resumable.

## Evidence to leave in Argus projects

Whenever infrastructure changes affect a benchmark run, record:

- `research/INFRA_STATUS.md` or an `INFRA_BLOCKER.md` with `amlt status`,
  SSH/tunnel probe, GPU probe, and frozen path checks.
- Which experiment/job/portal URL owns the reservation.
- Whether the issue is capacity (`queued`, no GPU) or environment parity
  (GPU reachable but frozen data/env missing).
- The next action: wait, resume, submit new job, restore data/env, or collect
  a completed run.

