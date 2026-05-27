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
- **Manifest, freshness, and validation-route drift:** Auto Research Pipeline owns the source graph; after regenerating content artifacts, run `repair-emnlp-contract-artifacts` when these failures appear together, or the narrower `refresh-manifest`, `refresh-artifact-freshness`, and `write-validation-priority-policy` tools for isolated drift. Do not hand-edit JSON status files.
- **Short/underfilled/weird papers:** EMNLP Format Preflight classifies the symptom; Auto Research Pipeline and Agent Research Benchmark Runner own the backward move to more experiments, ablations, failure analysis, robustness/public validation, or claim downgrade.
- **Final format/layout preflight:** EMNLP Format Preflight. Owns `FORMAT_PREFLIGHT.md`, compile cleanliness, page budget, References/Appendix order, table/figure style, code-like labels, and `validate-research-md-format`.
- **Academic prose review:** EMNLP Academic Language Review. Owns `ACADEMIC_LANGUAGE_REVIEW.json`, model-backed prose scoring, stale review checks, and concrete language directives.
- **Visual layout review:** Paper Review Revision Loop and EMNLP Format Preflight prepare layout; `paper_layout_review` and Research Submission Assurance Gate audit final PDF/page snapshots.
- **Submission readiness:** Research Submission Assurance Gate. Owns `SUBMISSION_ASSURANCE.json`, final PASS/FAIL/BLOCKED decision, and `validate-full-emnlp`.

## Validator Issue-Code Quick Route
When `validate-full-emnlp` fails, group the TSV output by issue code first. Repair the earliest upstream class below; do not spend a round polishing stale downstream JSON.

| Issue codes / symptoms | Primary route | Required action |
| --- | --- | --- |
| `missing_pipeline_state`, `missing_literature_grounding`, `missing_idea_provenance`, `missing_code_reuse_plan` | Auto Research Pipeline + Research Brief To Experiment Plan | Bootstrap the stage state, literature grounding, idea provenance, benchmark/source plan, and code reuse plan before drafting. |
| `missing_full_scale_experiment_run`, `missing_baseline_condition_run`, `incomplete_full_scale_experiment_run`, `underpowered_pilot`, `pilot_pdf_without_full_scale_evidence`, `synthetic_only_benchmark` | Agent Research Benchmark Runner | Run or collect full-scale source-backed evidence and nontrivial baselines; benchmark construction/status files alone are not experiment evidence. |
| `proposed_result_missing`, `missing_results_summary`, `quality_signal_contradicts_results`, unsupported or weak result claims | Research Results Analysis And Figures + Claims Evidence Audit | Recompute canonical result tables from raw runs, update result-to-claim mappings, then soften or supplement claims. |
| `claim_graph_*`, `supported_claim_missing_*`, `missing_paper_contribution`, `invalid_quality_signal`, `ready_quality_calibration_with_blocking_issues` | Claims Evidence Audit | Rebuild `CLAIM_GRAPH.json`, `EVIDENCE_GAPS.json`, and paper-quality calibration from current evidence before touching prose. |
| `rendered_main_body_underfilled`, `underlength_emnlp_paper`, `missing_midpaper_visual_pages`, `missing_main_content_pages`, `overlength_emnlp_paper` | EMNLP Paper Drafting + EMNLP Format Preflight; route back to experiments if evidence is thin | Fix page flow with literature-grounded Introduction/Related Work framing, benchmark/Method detail, supported analysis, ablations, failure studies, robustness/public-validation slices, or claim downgrade. References should begin on page 9 or later for an eight-page body. If Conclusion is before page 7 or References begin before page 9, add or move source-backed material before Conclusion; post-Conclusion limitations/release text does not count. Do not pad with generic prose or oversized floats. |
| `academic_language_missing_method_framework_or_runtime`, `academic_language_missing_method_model_identifier`, `academic_language_missing_method_agent_mechanism`, `academic_language_missing_method_evaluation_protocol`, `academic_language_mentions_internal_generation_infrastructure`, failed `method_system_readable` | EMNLP Paper Drafting + EMNLP Academic Language Review | Add reader-facing Method/Experimental Setup details about the evaluated paper system: framework/runtime or benchmark harness, model IDs/routes only when the evaluated system calls external models, agent mechanism, benchmark source, baselines, metrics, and budget. Remove Argus/Codex daemon, engineer/reviewer routing, academic-language/layout review, and image-tool details from paper method prose unless the paper is actually about that infrastructure. A compact system/configuration table is preferred over vague prose. |
| `severe_overfull_hbox`, `table_caption_missing_number`, `missing_float_inventory_target_section`, `body_float_missing_from_style_guide`, `float_inventory_label_not_in_body`, `missing_paired_significance_table` | EMNLP Format Preflight + EMNLP Paper Drafting | Repair the LaTeX source, split/resize tables, give every body float a style-guide entry and target section, reference it in text, and make captions state numerical or evidence-backed takeaways. For `table_caption_missing_number`, the caption must include the key numerical result. |
| `placeholder_bibtex_author_others`, `rendered_placeholder_reference_authors`, `citation_command_dumping`, missing or dumped citations | EMNLP Paper Drafting + EMNLP Format Preflight | Verify BibTeX metadata, replace `and others`/`et al.` placeholders with full author lists where required, distribute citations by claim/topic/paragraph, recompile, and inspect rendered references. |
| `conceptual_body_figure_not_image2`, `image2_conceptual_figure_not_included_in_main_tex`, `missing_image2_*`, `mismatched_image2_sidecar_prompt_sha256` | Research Results Analysis And Figures | Use the Argus image tool/image-2 route, preserve the exact accepted raster in `paper/main.tex`, and repair prompt/output/sidecar/inspect/review/provenance hashes. Do not redraw locally and wrap it in image-2 metadata. |
| `stale_layout_review_artifact`, `layout_review_not_pass`, `layout_review_not_visual`, `low_layout_review_score`, `missing_layout_review*`, `references_share_body_page` | EMNLP Format Preflight + Paper Review Revision Loop | Stabilize `main.tex` and `main.pdf`, inspect page images, then rerun `paper_layout_review --review-mode vision --write`. If References share a page with body end matter, fix the reference boundary only after the pre-Conclusion body is full; do not hand-edit layout review JSON to PASS. |
| `stale_academic_language_review_source`, `academic_language_review_not_pass`, `academic_language_evidence_quote_not_found`, `failed_academic_language_required_check`, `low_academic_language_*` | EMNLP Academic Language Review | Stabilize source first, revise prose against concrete directives, then rerun `academic_language_review --review-mode model --write`; evidence span quotes must exist in the reviewed source. |
| `artifact_*`, `missing_required_artifact_freshness_record`, `unknown_generated_artifact_source`, `generated_artifact_*`, `validation_failure_route_*`, `missing_validation_*` | Auto Research Pipeline | After content artifacts are regenerated, prefer `python -m argus_skill.skills.pipeline_contracts repair-emnlp-contract-artifacts --project-root .` for combined manifest/freshness/route drift; use the narrower helpers only for isolated drift. |
| `draft_not_submission_quality`, `draft_self_reports_not_submission_quality`, `missing_submission_assurance`, `submission_not_ready_verdict`, `submission_stage_not_successful` | Research Submission Assurance Gate | Run this last from current validators/reviews. `PASS`/`WARN` is invalid while evidence, format, claims, image-2, review, manifest, freshness, or final-gate blockers remain. |

## Repeated Failure Escape Hatch
- If the same test, validator issue code, or review-span failure repeats two times in a row, stop broadening guessed fallbacks or making sentence-level cosmetic edits.
- Capture the full failing command output, traceback/assertion, expected value, actual value, and the source fixture or artifact path that produced it. Route from that concrete assertion, not from the high-level validator name.
- For `pytest` failures in generated-paper fixtures, first decide whether the bug is production source drift, stale generated artifact, or a test fixture that no longer reflects the generator contract. Repair the smallest authoritative source and add one regression assertion for the contract being protected.
- If a synthetic fixture drives the loop, prefer making the fixture use the generator's real canonical phrases or explicit test inputs over relaxing production review logic repeatedly. Do not spend more than one additional cycle on guessed fallback terms.
- After the escape-hatch fix, rerun the narrow failing command once, then the broader validator only if the narrow command passes.

## Repair Principle
- If a paper looks thin, suspiciously formatted, or padded, first inspect evidence contracts: `validate-full-scale-evidence`, `CLAIM_GRAPH.json`, `EVIDENCE_GAPS.json`, and `VALIDATION_PRIORITY_POLICY.json`.
- Only use drafting/formatting repairs when the evidence already exists. Missing evidence routes to experiments, ablations, failure analysis, robustness/public validation, or claim downgrade.
