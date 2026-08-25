---
name: "Direct Kernel Engineering"
description: "Implement and measure one production kernel or inference improvement without framework paperwork."
---

# Direct Kernel Engineering

1. Read the smallest relevant repository instructions, implementation path, tests,
   and existing result that prevents duplicate work.
2. Reproduce the current behavior or baseline with the repository's own environment.
3. Identify the measured bottleneck and choose one coherent mechanism with a cheap
   falsification check.
   Proactively inspect current primary papers and official implementations whenever
   outside mechanisms could materially improve the plan. A bounded research mission
   may deliver a concise report only; it does not need code or an executable gate unless
   the task explicitly requests one.
4. Implement it in production code, preserving the public API and safe fallback.
5. Run correctness first, then comparable warm target-hardware measurements.
6. Retain only a verified improvement; otherwise revert or leave it disabled and
   state the concrete reason.

Do not create scope documents, algorithm plans, frontier ledgers, environment
reports, baseline protocols, outcome schemas, validation matrices, results reports,
or repeated checkpoints unless the operator explicitly requests one or a concise
result is genuinely needed by a later task.
