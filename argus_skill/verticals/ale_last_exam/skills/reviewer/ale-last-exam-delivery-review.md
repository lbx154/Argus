---
name: "ALE Last-Exam Delivery Review"
description: "Independently verify Agents' Last Exam outputs before exit, prioritizing must-pass conditions, exact paths, complete file sets, parseability, native-tool evidence, measured values, and consistency without access to hidden references."
---

## Review protocol

Treat the original task instruction as the only statement of what is required.
The hidden reference and final grader are unavailable and must remain so. Do not
award completion from the engineer's narrative.

Check in this order:

1. Enumerate every mandatory output and every condition that fails the
   submission outright, from the original instruction.
2. Inspect exact paths, names, extensions, sizes, timestamps, and companion files.
3. Reopen or parse each output file with a task-appropriate independent check. A
   correctly named but corrupt, empty, all-null, placeholder, or structurally
   invalid file fails.
4. Verify that required simulations, renders, exports, builds, or replays really
   finished and that reported values come from their outputs.
5. Inspect native application/project state and visual evidence when the
   instruction requires it. Confirm screenshots and previews depict the
   submitted work.
6. Cross-check all output files for stale exports and contradictory values.
7. Make a final pass over the complete set of outputs against the must-pass
   conditions.

Return `continue` whenever one observable requirement remains unverified. Put the
highest-risk repair first and give the engineer exact commands or UI checks where
possible. Return `done` only after independently verifying the complete set of
outputs.
