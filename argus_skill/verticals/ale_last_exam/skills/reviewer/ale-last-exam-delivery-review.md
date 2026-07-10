---
name: ALE Last-Exam Delivery Review
description: Independently audit Agents' Last Exam deliverables before exit, prioritizing hard gates, exact paths, complete bundles, parseability, native-tool evidence, measured values, and consistency without access to hidden references.
category: computer-use-review
version: 1
scientist_model: gpt-5.5
created_at: 2026-07-10T00:00:00+00:00
---

## Review protocol

Treat the original task instruction as the sole contract. The hidden reference
and final grader are unavailable and must remain so. Do not award completion from
the engineer's narrative.

Audit in this order:

1. Enumerate every mandatory output and hard gate from the original instruction.
2. Inspect exact paths, names, extensions, sizes, timestamps, and companion files.
3. Reopen or parse each artifact with a task-appropriate independent check. A
   correctly named but corrupt, empty, all-null, placeholder, or structurally
   invalid file fails.
4. Verify that required simulations, renders, exports, builds, or replays really
   finished and that reported values come from their outputs.
5. Inspect native application/project state and visual evidence when the contract
   requires it. Confirm screenshots and previews depict the submitted work.
6. Cross-check all artifacts for stale exports and contradictory values.
7. Run a final hard-gate pass over the whole bundle.

Return `continue` whenever one observable requirement remains unverified. Put the
highest-risk repair first and give the engineer exact commands or UI checks where
possible. Return `done` only after independently verifying the complete bundle.
