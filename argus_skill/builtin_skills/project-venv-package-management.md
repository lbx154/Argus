---
name: Project Venv Package Management
description: Every project owns its own ./.venv. The agent may install any Python package it needs into the project venv at any stage. Never install into the Argus framework venv.
category: engineering
priority: critical
version: 1
created_at: 2026-05-28T00:00:00+00:00
---

# Project Venv Package Management

## TL;DR

Every project has its own `./.venv/` under the project root. **You may
freely install any package you need into it, at any stage, on any
round.** No approval, no committee. The only iron rule: it goes into
the project venv, never into the Argus framework venv.

```bash
# Always use the project venv's pip:
./.venv/bin/pip install <package>

# Always use the project venv's python:
./.venv/bin/python <script.py>
```

## When to use this skill

**Every stage. Every round.** Research, plan, benchmark, run, analysis,
draft, review, submission — if you hit any of the following, install
the missing package immediately:

- `ModuleNotFoundError: No module named '<x>'`
- `ImportError: cannot import name '<x>'`
- A README example for your chosen framework uses a package you don't
  have
- The shortlisted training/inference framework needs a CUDA / triton /
  flash-attention / xformers extension
- A benchmark loader needs an evaluator dependency (clip, fairseq,
  detector backbones, …)
- A figure script needs matplotlib / plotly / seaborn / cairosvg
- A new PDF / TeX / image tool is needed for draft or review

Do NOT:

- Apologize for needing to install something
- Open an issue / wait for the operator
- Switch to a worse approach to avoid the install
- Fall back to a stub / mock / oracle because "the package is missing"

## The two-rule contract

### Rule 1 — Use the project venv

The project venv is always at `<project-root>/.venv/`. Always use the
absolute paths from the project root:

```bash
./.venv/bin/python       # ← the project's Python interpreter
./.venv/bin/pip          # ← the project's pip
./.venv/bin/pytest       # ← installed via project venv if needed
./.venv/bin/accelerate   # ← installed via project venv if needed
```

If `which python` does not resolve to `<project>/.venv/bin/python`, you
are in the wrong shell context. Either `source .venv/bin/activate`
once, or prefix every command with `./.venv/bin/python` / `./.venv/bin/pip`.

### Rule 2 — Never touch the Argus framework venv

The Argus framework Python is at `${ARGUS_SKILL_PYTHON}` (typically
`/root/argus-skill/.venv/bin/python`). It runs the daemon, the
reviewer, the planner, the validators, the helper CLIs. Installing
project dependencies there pollutes every other project that shares
this host. **Do not run**:

```bash
# ❌ FORBIDDEN
${ARGUS_SKILL_PYTHON} -m pip install <anything>
pip install <anything>          # if `pip` is the framework one on PATH
sudo pip install <anything>
```

Use `./.venv/bin/pip` instead. Always.

## The project venv is allowed to inherit host packages

The launcher creates the project venv with `--system-site-packages`,
so anything pre-installed at the host level (typically `torch` with
CUDA on an ML-ready host) is already visible from inside the project
venv. You do **not** need to reinstall torch / CUDA / triton in that
case. Verify with:

```bash
./.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

If torch is missing entirely (non-ML host), install it into the project
venv. If torch is present but broken, install a fresh wheel into the
overlay; pip will shadow the host copy with the project copy.

## Versioned-overlay tactic for broken host packages

When the host site-packages has a broken / outdated copy of a package
you also need (this happened in v2 with `accelerate`), install a
fresh wheel into the project venv — overlay wins:

```bash
./.venv/bin/pip install --upgrade accelerate
./.venv/bin/python -c "import accelerate; print(accelerate.__version__, accelerate.hooks)"
```

## Record what you installed

After each batch of `pip install`, run:

```bash
./.venv/bin/pip freeze > experiments/PIP_FREEZE.txt
```

so future rounds (and the reviewer) can see the exact dependency
snapshot. This is much cheaper than wondering "why does it work
locally but break in the subagent" later.

## Reviewer hook

If the round summary says "skipped because dependency missing", or the
engineer produced a stub function because `import X` failed, the
reviewer must `continue` and explicitly tell the engineer:

> Install `<package>` into the project venv with
> `./.venv/bin/pip install <package>` and retry; do not fall back to
> stubs.

A reply of "I noticed `<package>` was missing so I worked around it" is
never acceptable. The work-around is the install.
