---
name: Dependency-Aware Task Decomposition
description: Convert a project objective and current evidence into a minimal dependency-aware set of executable tasks with decisive acceptance checks.
---

# Dependency-Aware Task Decomposition

Use when an objective is too large or coupled for one coherent Engineer turn.

1. Inspect current project state and completed work before proposing tasks.
2. Split only at real dependency, ownership, environment, or verification
   boundaries; do not create ceremonial planning/reporting stages.
3. Give each task one executable objective, one decisive acceptance check,
   explicit non-goals, and only the context paths it must read.
4. Encode dependencies directly. Keep independent work parallelizable and avoid
   scheduling a task whose prerequisite evidence does not exist.
5. Reuse or revise pending work instead of emitting renamed duplicates. Preserve
   negative results and route a method failure to a genuinely different next
   mechanism.
