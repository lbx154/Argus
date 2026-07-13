# Task 01: Completion-Livelock Minimal Fix

Read these files first:

- `AGENTS.md`
- `docs/goals/2026-07-12-completion-livelock-night-agent-design.md`
- `docs/goals/2026-07-12-completion-livelock-night-agent-plan.md`
- `docs/experiment/2026-07-12-completion-livelock/README.md`

Execute every checklist item in the implementation plan using strict TDD.
Keep production changes limited to:

- `argus_skill/manager/_core.py`
- `argus_skill/life/supervisor/_planning_cycle.py`

Tests and the two documentation ledgers may also change. Do not refactor
unrelated code, alter prompts or schemas, weaken the paper certification gate,
commit, or push.

When a design assumption is contradicted by current code, stop and state the
conflict clearly in the tmux session instead of improvising a broader change.

Done means all focused and broader commands in the plan pass, `git diff --check`
passes, and the experiment/status documents contain the exact evidence.

