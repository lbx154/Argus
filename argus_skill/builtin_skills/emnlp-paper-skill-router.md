---
name: EMNLP Paper Skill Router
description: Route EMNLP/ACL paper repair work to the right built-in skill without duplicating long paper, citation, figure, evidence, and validation rules.
category: skill-routing
version: 1
scientist_model: gpt-5.4
created_at: 2026-05-26T00:00:00+00:00
---

## Title
EMNLP Paper Skill Router

## When to use
- Use this first when a paper task mentions unclear ownership: references, image-2 figures, short/underfilled papers, weird formatting, failed validators, stale artifacts, or submission readiness.
- Use this to decide which focused skill to load next. Do not treat this router as a substitute for the target skill's full contract.

## Routing
- **End-to-end stage control:** Auto Research Pipeline. Owns `research/PIPELINE_STATE.json`, stage gates, pivots, and when to move backward from drafting to experiments.
- **Literature, idea, benchmark plan:** Research Brief To Experiment Plan. Owns literature/source discovery, `LITERATURE_GROUNDING.json`, `IDEA_PROVENANCE.json`, `CODE_REUSE_PLAN.json`, benchmark provenance, and baseline plan.
- **Experiment execution:** Agent Research Benchmark Runner. Owns live runs, `progress.jsonl`, `status.json`, raw scored rows, full-scale task count, baselines, ablations, STOP-file protocol, and resumability.
- **Results, tables, plots, image-2 manifest:** Research Results Analysis And Figures. Owns `RESULTS_REPORT.md`, `result_to_claim.tsv`, `results_table.tsv`, data-derived figures/tables, Figure 1 / teaser / overview image-2 generation, and `IMAGE2_FIGURES.json`.
- **Paper structure and first LaTeX draft:** EMNLP Paper Drafting. Owns official ACL template use, `PAGE_BUDGET.md`, `PAPER_DRAFT_REPORT.json`, verified BibTeX insertion, citation placement, claim-to-evidence prose, and `main.tex`.
- **Style exemplars:** Paper Exemplar PDF Learning. Owns downloaded exemplar PDFs/text, style profile, suitability selection, structure blueprint, and conformance artifacts.
- **Citation and reference correctness:** EMNLP Paper Drafting creates/repairs verified bibliography entries; EMNLP Format Preflight and Research Submission Assurance Gate audit final formatting, citation depth, rendered reference shape, and citation dumping.
- **Short/underfilled/weird papers:** EMNLP Format Preflight classifies the symptom; Auto Research Pipeline and Agent Research Benchmark Runner own the backward move to more experiments, ablations, failure analysis, robustness/public validation, or claim downgrade.
- **Final format/layout preflight:** EMNLP Format Preflight. Owns `FORMAT_PREFLIGHT.md`, compile cleanliness, page budget, References/Appendix order, table/figure style, code-like labels, and `validate-research-md-format`.
- **Academic prose review:** EMNLP Academic Language Review. Owns `ACADEMIC_LANGUAGE_REVIEW.json`, model-backed prose scoring, stale review checks, and concrete language directives.
- **Visual layout review:** Paper Review Revision Loop and EMNLP Format Preflight prepare layout; `paper_layout_review` and Research Submission Assurance Gate audit final PDF/page snapshots.
- **Submission readiness:** Research Submission Assurance Gate. Owns `SUBMISSION_ASSURANCE.json`, final PASS/FAIL/BLOCKED decision, and `validate-full-emnlp`.

## Repair Principle
- If a paper looks thin, suspiciously formatted, or padded, first inspect evidence contracts: `validate-full-scale-evidence`, `CLAIM_GRAPH.json`, `EVIDENCE_GAPS.json`, and `VALIDATION_PRIORITY_POLICY.json`.
- Only use drafting/formatting repairs when the evidence already exists. Missing evidence routes to experiments, ablations, failure analysis, robustness/public validation, or claim downgrade.
