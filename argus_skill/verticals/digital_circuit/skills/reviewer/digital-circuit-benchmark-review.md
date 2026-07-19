---
name: Digital Circuit Benchmark Review
description: Audit fixed-harness RTL benchmark runs for isolation, anti-cheating, immutable Pass@1 evidence, bounded repair, and reproducible aggregate claims.
category: anti-cheat
version: 1
---

# Digital Circuit Benchmark Review

## Review protocol

1. Verify the dataset hash, task selection, score definition, evaluator/tool versions, model/backend, budget, and repair cap were frozen before results.
2. Confirm each task used a unique workspace and that concurrent agents never wrote the same workspace.
3. Confirm the agent received only allowed prompt/context: no golden patch/output, hidden harness source, other task solution, or network-derived answer.
4. Confirm every prompt-referenced pre-existing public file was present before generation. Reject and label the task as a packaging defect when a referenced specification/context file is absent; do not allow repeated oracle failures to reconstruct that missing contract.
5. Require a non-empty RTL patch and real official harness execution in a fresh output prefix.
6. Treat the first official result as immutable Pass@1 evidence. Repairs must append new records and must never rewrite the first attempt.
7. For oracle-guided repair, verify only the allowed failure log crossed the isolation boundary and the repair stayed within the declared cap.
8. Recompute aggregate metrics from the attempt ledger. Do not divide attempt successes by attempt count and call that Pass@1; Pass@1 has one first-attempt row per task.
9. Keep post-repair success, cost, elapsed time, and failure taxonomy separate from the official first-attempt headline.
10. Reject stale image/patch reuse, mutable scorer inputs, concurrent shared Docker scoring, missing raw evidence, or selective omission of failed tasks.
11. Compare agents only on the same frozen task set, scorer, model class, budget, toolchain, and repair policy.
