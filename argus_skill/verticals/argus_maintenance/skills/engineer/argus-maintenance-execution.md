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
6. Run the focused regression and affected suite.

Do not create a new abstraction unless the patch already has more than one real user.
