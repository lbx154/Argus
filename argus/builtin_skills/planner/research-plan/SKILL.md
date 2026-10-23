---
name: "The research plan"
description: "How the Planner keeps RESEARCH_PLAN.md: when to create it, what it holds, and how plan_update replaces it."
---

# The research plan

Keep `RESEARCH_PLAN.md` in daemon state. Create it when it is absent or invalid,
from the Manager brief, OBJECTIVE.md and the journal; update it for new
hypotheses, results or direction changes. `plan_update` replaces it with full
Markdown; leaving it out keeps it.

Keep it under about 300 lines, in this order:

1. `# Research plan` and a one-sentence objective.
2. `## Central hypotheses`: numbered; untested, supported, refuted or
   abandoned; each with a one-line evidence pointer.
3. `## Experiment program`: the most informative next experiments and why, with
   no fixed numeric pass/fail thresholds.
4. `## Established results`, with evidence.
5. `## Dead ends`: attempts and why they were abandoned.
6. `## Next milestone`: the scientific gain toward a scoped paper.

Preserve Dead ends; prune repetition when truncation is reported.
