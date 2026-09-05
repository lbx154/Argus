---
name: "Argus Maintenance"
description: "Inspect and simplify Argus with small reusable changes and clear core/vertical ownership."
---

# Argus maintenance

1. Read the real call path and closest existing implementation.
2. Run `python -m argus_skill.verticals.argus_maintenance.architecture_audit` when useful.
3. Treat findings as candidates; change only those relevant to the task.
4. Remove dead wrappers, stale aliases, duplicated state, unjustified literals, and silent fallback chains.
5. Put generic orchestration in core and domain behavior in the owning vertical.
6. Run the focused regression and affected suite from the worktree root with the running
   framework interpreter: `"${ARGUS_SKILL_PYTHON:-python3}" -m pytest <target> -q`. By
   default the worktree cwd shadows the deployed package; if the environment sets
   PYTHONSAFEPATH (the safe-mode sandbox does), prefix the command with
   PYTHONPATH="$PWD" so the worktree code is what runs. Either way, testing never
   needs a pip install of the framework.
7. Never install the framework package: no `pip install -e .`, no `--user`, and nothing
   else that writes user site-packages or `~/.local/bin`. A user-level install rewrites
   the shared `argus` launcher to point at this worktree and hijacks every later
   deployment; shipping goes only through the deploy boundary.
8. Install an extra third-party dependency, when the task truly needs one, into a
   throwaway venv inside the worktree or with `pip install --target <worktree dir>`.

Do not create a new abstraction unless the patch already has more than one real user.
