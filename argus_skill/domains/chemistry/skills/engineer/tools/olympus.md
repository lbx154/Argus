---
name: Olympus Reaction Optimization
description: Use Olympus experimental-emulator surfaces and planners for reproducible closed-loop chemistry optimization studies.
category: chemistry-tool-olympus
version: 1
---

Use <https://github.com/aspuru-guzik-group/olympus>. The documented package name
is `olymp` (`pip install olymp`), not `olympus`; verify current compatibility
before installation.

Probe one documented dataset/emulator/planner combination. Retain repository or
package version, dataset identity, parameter bounds and types, emulator, planner,
budget, initialization, seeds, per-step proposals/observations, timing, and
failures.

Olympus surfaces derive from prior experiments or emulators; they are not new
physical measurements. Use equal budgets and label online agent control,
agent-designed frozen policies, and conventional planners separately.
