---
name: "Verify a Recorded Blocker"
description: "核实记录中的阻塞是否仍存在。 Recheck a potentially stale blocker with the cheapest decisive authorized probe; distinguish still blocked, cleared and insufficient evidence, then continue permitted work."
---

# Verify a Recorded Blocker

Use when a prior record says a specific action is blocked. Treat that record as a
hypothesis; do not re-investigate the whole project or repeat a costly failed run.

1. Translate the blocker into the relevant condition: resource access, artifact
   validity, missing decision, dependency version or a specific verification result.
2. Choose the cheapest authorized probe that can decide it. Existing fresh evidence,
   a file check, structured field, small access request or one targeted test may suffice.
3. Run or inspect the probe and record the scope, actual outcome and coverage limits.
   File existence does not establish validity; metadata visibility does not grant
   download permission; missing telemetry does not establish missing execution.
4. Classify the result:
   - `CLEARED`: the action's actual prerequisite is now established.
   - `STILL_BLOCKED`: direct evidence establishes the prerequisite is not satisfied.
   - `INSUFFICIENT_EVIDENCE`: the probe failed to distinguish the possibilities.
5. On inconclusive evidence, choose the next inexpensive discriminating observation
   within budget, or explain the missing evidence. Do not guess a binary verdict.
6. When cleared, resume the smallest useful permitted action. When still blocked,
   preserve evidence and continue independent work; route only a concrete unavailable
   decision/resource through the current role. A status check is not authority to
   edit completion gates or override project state.

Report the current condition, decisive evidence and next action in the normal task
response. Do not create an open-ended watcher, extra evidence packet or repeated
permission request for an action already authorized.
