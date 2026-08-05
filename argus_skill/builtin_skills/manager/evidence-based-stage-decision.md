---
name: Evidence-Based Stage Decision
description: Turn Reviewer and Planner evidence into a concise Manager hold, advance, rollback, or completion decision without redoing execution work.
---

# Evidence-Based Stage Decision

Use when a reviewed mission or Planner verdict may change lifecycle stage.

1. Read the current stage contract and the latest independent verdict.
2. Separate an incomplete current deliverable from an upstream defect, an
   external blocker, and a completed bounded increment inside an unfinished
   project.
3. Choose the smallest valid transition: hold for current-stage repair, rollback
   to the earliest broken stage, advance when the next stage is unlocked, or
   complete only when the final contract is satisfied.
4. Preserve operator scope and unfinished DAG work. Do not implement the repair,
   rewrite the verdict, or invent evidence.
5. State the decisive evidence and target stage briefly so Planner and Engineer
   can act without reconstructing the ruling.
