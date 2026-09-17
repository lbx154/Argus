---
name: "Project Environment and Dependencies"
description: "处理项目解释器、依赖和缓存问题。 Diagnose interpreter/package conflicts and use the existing project environment, lockfiles and allocated caches without modifying the Argus framework environment."
---

# Project Environment and Dependencies

Use when a task needs an interpreter, package or model/data cache. A missing import
is a symptom: first check the selected executable, package/version, local module
shadowing and the project's documented setup.

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

Update the existing dependency manifest/lockfile when the task changes dependencies.
Use a freeze snapshot only when no stronger project mechanism exists and
reproduction needs it. Report a concrete platform, license, access or budget blocker
when installation is not feasible; neither invent a working stub nor retry an
unchanged failing installation. Stop after the original operation and its relevant
check succeed.
