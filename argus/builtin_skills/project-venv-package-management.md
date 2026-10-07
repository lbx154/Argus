---
name: "Project Environment and Dependencies"
description: "处理项目解释器、依赖和缓存问题，并在出结论前核实实验真正用到的资源。 Diagnose interpreter/package conflicts, use the existing project environment, lockfiles and allocated caches without modifying the Argus framework environment, and verify only the resources a claim-bearing run actually uses before producing evidence."
---

# Project Environment and Dependencies

Use when a task needs an interpreter, package or model/data cache, and before a
run whose result will be claimed. A missing import is a symptom: first check the
selected executable, package/version, local module shadowing and the project's
documented setup.

## Preserve the project's environment contract

1. Read the repository's setup instructions, dependency manifest and lockfile.
   Reuse its uv, Conda, container, virtualenv or other configured environment.
   Do not add Python or a `.venv` to a project that does not need it.
2. Run project code with that environment's interpreter. For an existing Python
   venv use `.venv/bin/python` on POSIX or `.venv/Scripts/python.exe` on Windows;
   obtain the absolute path from the actual project root.
3. If no environment exists and Python is needed, create one using a compatible
   base interpreter and the project's package manager. Use `--system-site-packages`
   only when the project deliberately inherits an approved host installation.
4. Install only dependencies needed by the task, honoring version constraints and
   lockfile workflow. Do not install an entire ML stack for an unrelated job, or
   upgrade packages before diagnosing an import/signature conflict.
5. Run Argus helper CLIs with the supplied `ARGUS_SKILL_PYTHON`. That framework
   environment is not a destination for project dependencies. Never install into
   it or use `sudo pip` to repair project imports.

Use `python -m pip` with the selected interpreter where pip is the project's
manager; bare `pip` may belong to another environment. Verify the actual import or
minimal failing operation after the repair, not just the installer exit code.

## Resources and caches

Honor the current resource allocation and configured cache paths. Reuse an approved
external cache when supplied; otherwise choose a project-managed, ignored location
that fits disk capacity. Do not move or duplicate weights merely to satisfy a
hardcoded directory convention. Keep generated caches and credentials out of Git.
For GPU work, preserve `CUDA_VISIBLE_DEVICES`; logical `cuda:0` is the first visible
device, not necessarily physical GPU 0. A venv does not allocate a GPU.

## Verify what a claim-bearing run actually uses

An environment you have not verified is a hypothesis, not a fact, and a result
produced on a hypothesis is not evidence yet. Before the first real benchmark or
evidence-producing run, and again before a pilot, full run or ablation that
differs substantively from the last one, confirm that what the run depends on
is present and behaves: the interpreter and dependencies import from the project
environment rather than the framework one; the public dataset, benchmark or
evaluator release is the one the claim names, at a recorded version, and the
official evaluator or metric implementation gives a plausible non-empty result
on a tiny known input; the compute the run needs is allocated and visible to
the framework; checkpoints match their declared revision; each external route
the run will call answers once, without credentials in the log; the output
directory is writable with room for what the run will write; and for
long-running work, the log, progress and cancellation paths exist.

Check only the resources this implementation uses. A CPU-only, API-only or
theoretical experiment needs no CUDA, weights or Hugging Face cache, and
inventing such a dependency to satisfy a form misleads as much as skipping a
real one. Verify a custom runtime, trainer or evaluator against a trusted
reference rather than rejecting it by category. Report a concrete blocker with
the decisive command or output in the normal response; do not write a separate
preflight file. A Reviewer keeps the stage open when a check the run depended
on is missing, stale, contradictory or failed, and does not demand a resource
the experiment never used.

Update the existing dependency manifest/lockfile when the task changes dependencies.
Use a freeze snapshot only when no stronger project mechanism exists and
reproduction needs it. Report a concrete platform, license, access or budget blocker
when installation is not feasible; neither invent a working stub nor retry an
unchanged failing installation. Stop after the original operation and its relevant
check succeed.
