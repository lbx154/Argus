---
name: EMNLP Paper Skill Router
description: Choose the smallest relevant skill for EMNLP/ACL paper work without loading the whole paper pipeline.
category: skill-routing
version: 2
created_at: 2026-07-21T00:00:00+00:00
---

# EMNLP Paper Skill Router

Load only the skill needed for the current scientific or writing problem:

- literature, research question, or experiment design: **Research Brief To
  Experiment Plan**;
- real benchmark execution and baselines: **Research Experiment Runner**;
- analysis, tables, and figures: **Research Results Analysis And Figures**;
- unsupported or stale claims: **Claim Check**;
- first ACL-format manuscript: **EMNLP Paper Drafting**;
- page limits, references, and LaTeX defects: **EMNLP Format Preflight**;
- reader-facing revision: **Paper Review Revision Loop**;
- final independent judgment: **Final Paper Review**.

Use language, infrastructure-leak, or visual-layout review tools only when the
Reviewer has a concrete doubt. Their JSON outputs are optional feedback, not
pipeline currency.

When a paper looks weak, decide whether the cause is missing research, missing
analysis, unclear writing, or layout. Route to that source problem; do not create
manifests, freshness ledgers, question inventories, or assurance packets as a
substitute.
