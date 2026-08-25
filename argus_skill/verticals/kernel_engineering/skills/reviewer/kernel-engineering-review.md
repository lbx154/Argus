---
name: "Direct Kernel Review"
description: "Review actual kernel code, correctness, and benchmark evidence without requiring process documents."
---

# Direct Kernel Review

Review the implemented change and the evidence that decides it:

- the intended code path is exercised;
- the public API and fallback remain correct;
- tests cover relevant shapes, dtypes, and failure boundaries;
- baseline and candidate measurements use comparable warm conditions;
- latency, memory, and other claimed benefits exceed noise without hidden regressions.
- a report-only research mission is judged on primary-source quality, factual accuracy,
  synthesis, and usefulness to the next engineering decision; code is not required.

Request a repair only for a concrete code or evidence defect. Never block completion
because a framework-specific scope, frontier, environment, baseline, outcome,
validation, report, or checkpoint file is absent.
