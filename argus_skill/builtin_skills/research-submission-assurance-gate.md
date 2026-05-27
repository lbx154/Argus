---
name: Research Submission Assurance Gate
description: Decide whether a research draft can be called EMNLP/ACL submission-ready by checking experiment integrity, result-to-claim support, paper claims, citations, prose quality, layout, strongest rejection arguments, and package completeness.
category: research-audit
version: 1
scientist_model: gpt-5.4
created_at: 2026-05-23T00:00:00+00:00
---

## Title
Research Submission Assurance Gate

## Description
Run the final high-strictness gate before a paper is described as EMNLP-ready. This adapts ARIS experiment-audit, result-to-claim, paper-claim-audit, citation-audit, strongest-rejection review, paper-quality calibration, and MIT-licensed `AI-Research-SKILLs` rigor/prose workflow concepts into a single argus-skill assurance contract.

## When to use
- A draft exists under `paper/` and the operator asks whether it is ready, submission-quality, conference-ready, or safe to polish.
- `research/NARRATIVE_REPORT.md`, experiment artifacts, and a claims-evidence table exist.
- The pipeline is about to report success after paper drafting or revision.

## When NOT to use
- There are no raw experiment artifacts yet; run or plan experiments first.
- The operator only asks for a quick writing pass and explicitly does not want a readiness judgment.
- A prior assurance report already blocks on missing evidence and nothing has changed.

## Required inputs
- `research/PIPELINE_STATE.json`
- `research/NARRATIVE_REPORT.md`
- `research/CLAIMS_TO_TEST.md`
- `experiments/BENCHMARK_PROVENANCE.md`
- `experiments/**/manifest.json`, raw results, logs, and verifier outputs
- `paper/main.tex` and `paper/main.pdf` when LaTeX is available
- `paper/artifacts/claims_evidence.tsv` or `paper/CLAIMS_EVIDENCE_AUDIT.tsv`
- `paper/ARTIFACT_MANIFEST.json` with canonical sources, generated artifacts, SHA-256 digests, TSV column schemas, and source links for manuscripts/reports/figures
- `paper/PAGE_BUDGET.md`, `paper/TEMPLATE_SOURCE.md`, and a thick `paper/style_ref/STYLE_PROFILE.md`
- `paper/main.log`, rendered `paper/main.pdf`, and extracted layout/page evidence sufficient to prove the `research.md` formatting preflight: no unresolved references/citations, no `[?]`, no `Overfull \hbox > 5pt`, body visibly fills the EMNLP long-paper budget (Conclusion not before page 7 and References/Appendix not before page 9 when PDF text can be extracted), conclusion by page 8 with no forced manual page break immediately before it, Limitations/Ethical Considerations present after the conclusion, References before Appendix, no total-page maximum after References/Appendix begin, anonymous author block, no placeholders, no `% UNVERIFIED` bibliography entries unless explicitly accepted by the operator, at least 35 verified BibTeX entries, at least 30 unique cited keys, at least two rendered References pages when PDF text can be extracted, every figure labeled/referenced, every table caption a numerical headline, at least one figure/table on each of pages 4--7, at least one paired-significance table when comparative binary outcomes are reported or an explicit not-applicable rationale, and complete reproducibility appendix
- `research/LITERATURE_GROUNDING.json` with at least 10 recent high-quality papers, 3 classic anchor papers, and recorded news/trend discovery signals
- `research/IDEA_PROVENANCE.json` proving the selected idea was derived from surveyed papers, benchmarks, trend signals, and code sources rather than agent brainstorming
- `research/CODE_REUSE_PLAN.json` recording official paper-code/open-source repository search, license/terms, attribution, and reuse/adapt/reject decisions
- Full-scale experiment evidence that passes `python -m argus_skill.skills.pipeline_contracts validate-full-scale-evidence --project-root .`: completed raw scored rows under `experiments/**` for every required method/baseline condition, not merely `benchmarks/full/tasks.jsonl`, a benchmark manifest, or a declared `status.json task_count`.
- `paper/style_ref/EXEMPLAR.json` with `exemplar_schema_version: 2`, at least two excellent open-access paper exemplars, local downloaded PDFs under `paper/style_ref/exemplars/`, text extracts, PDF SHA-256 digests, license/storage-policy metadata, and structural-style-only/no-prose-copy attestations
- `paper/PAPER_DRAFT_REPORT.json` declaring `target_venue: "EMNLP"`, `paper_scope: "long-paper"`, `main_content_pages`, `official_acl_template: true`, and `submission_quality_self_assessment`
- `paper/figures/IMAGE2_FIGURES.json` proving every non-data paper-facing figure was generated through image-2 / codex-image2 and that `paper/main.tex` includes the manifest's raster `output_path`; include prompt, raw generation sidecar, inspect sidecar, generation provenance, SHA-256, dimensions, and model-backed review sidecars. Data/metric/result plots may be script/vector generated from canonical artifacts; secondary non-data TikZ/pgfplots/matplotlib/PIL/SVG diagrams may not. **Self-drawn non-data figure replacements are hard blockers:** matplotlib/FancyBboxPatch, TikZ node graphs, SVG/PIL/HTML canvases, cleaned PDFs, screenshots, Inkscape/manual vector output, generic raster mockups, manual-only reviews, hand-written `codex-image2` manifests, or local PNG/JPEGs falsely labeled as `codex-image2` are not acceptable substitutes.
- `paper/FORMAT_PREFLIGHT.md` plus a clean `python -m argus_skill.skills.pipeline_contracts validate-research-md-format --project-root .` run proving the dedicated `research.md` format preflight passed
- `paper/ACADEMIC_LANGUAGE_REVIEW.json` and `paper/ACADEMIC_LANGUAGE_REVIEW.md` produced by `python -m argus_skill.skills.academic_language_review --project-root . --review-mode model --write`, with fresh source hashes, quoted evidence spans, score at least 4/5, and `needs_revision: false`
- `paper/PAPER_INFRASTRUCTURE_REVIEW.json` and `paper/PAPER_INFRASTRUCTURE_REVIEW.md` produced by `python -m argus_skill.skills.paper_infrastructure_review --project-root . --review-mode model --write`, with fresh source hashes, quoted evidence spans, score at least 4/5, `leak_free: true`, and `needs_revision: false`
- `paper/LAYOUT_REVIEW.json` and `paper/LAYOUT_REVIEW.md` produced by `python -m argus_skill.skills.paper_layout_review --project-root . --review-mode vision --write`, with rendered page snapshots, fresh hashes, score at least 4/5, and `needs_revision: false`
- Published-positive metadata and local PDF/text exemplar evidence from official sources when available. For EMNLP 2025, use the official awards page metadata for `Infini-gram mini: Exact n-gram Search at the Internet Scale with FM-Index` and the outstanding-paper list as positive quality-signal sources. Download open-access PDFs only as redistributable artifacts or local research-cache references according to their license/terms; do not copy paper prose.

## Assurance layers
Use the 6-state verdict schema `PASS | WARN | FAIL | BLOCKED | ERROR | NOT_APPLICABLE` for every layer.

1. **experiment integrity**
   - Run `python -m argus_skill.skills.pipeline_contracts validate-full-scale-evidence --project-root .` and require it to pass before any assurance `PASS`/final-ready verdict.
   - Check that evaluation uses real ground truth or a documented oracle, not another model's output as hidden ground truth.
   - Verify task pairing, seeds, model IDs, metrics, filtering, and failed-run handling match the experiment plan.
   - Verify that full-run task counts are unique semantic tasks, not repeated pilot rows with changed IDs. Inspect benchmark JSONL/records for duplicate prompts/specs/gold answers and suffix-copy patterns such as `_r2`, `_copy`, `_dup`, or equivalent relabeling.
   - Hard blockers: phantom result rows, missing raw artifacts for reported numbers, unpaired baselines, silent dropped failures, benchmark construction presented as execution, any required condition missing from the executed multi-source matrix, same-family-only evidence, or benchmark scale inflated by duplicate/relabelled episodes.

2. **result-to-claim**
   - Map every intended claim to raw evidence.
   - Verdicts per claim: `supported`, `weak`, `rejected`, `missing`, or `contradicted`.
   - Hard blockers: any strong comparative or numeric claim with `missing` or `contradicted` evidence.

3. **paper-claim audit**
   - Cold-read `paper/main.tex` against raw TSV/JSONL artifacts.
   - Check every number, percentage, comparison, superlative, figure caption, table caption, and scope phrase.
   - Run `python -m argus_skill.skills.pipeline_contracts validate-manifest --project-root .` and treat digest drift, TSV schema mismatch, unknown generated sources, source cycles, or missing manifest entries as paper-claim audit failures.
   - Hard blockers: unsupported SOTA/generalization language, mismatched numbers, stale manuscript copies relative to canonical tables, or pilot results written as full-benchmark conclusions.

4. **idea provenance and code reuse**
   - Run `python -m argus_skill.skills.pipeline_contracts validate-idea-provenance --project-root .` and `validate-code-reuse`.
   - Check that the final idea comes from surveyed papers, benchmark gaps, trend sources, and/or code sources--not from free-form agent brainstorming.
   - Check that official paper code, benchmark repositories, Papers with Code links, GitHub project pages, dataset repos, and relevant libraries were searched before implementation.
   - Confirm any reused/adapted code has license/terms, attribution, and a clear reuse decision; incompatible code must be rejected, not pasted.
   - Hard blockers: missing `research/IDEA_PROVENANCE.json`, `agent_generated` ideas, fewer than 3 literature-derived candidates, selected ideas without paper-derived sources, missing `research/CODE_REUSE_PLAN.json`, no repository/code search, or reused code without license/attribution.

5. **literature and exemplar grounding**
   - Run `python -m argus_skill.skills.pipeline_contracts validate-grounding --project-root .` and `validate-exemplar`.
   - Check that recent high-quality papers, classic anchors, and trend/news signals all exist before accepting related-work or motivation framing.
   - Trend/news signals do not need paper/benchmark/code backing merely to be recorded. However, any technical paper claim inspired by a trend must still be supported by surveyed papers/code/benchmarks or local experiment artifacts.
   - Confirm Paper Exemplar PDF Learning ran: at least two PDFs are downloaded locally, text extracts exist, `pdf_sha256` matches, one exemplar is a recent best/outstanding/award paper when available, and `paper/style_ref/STYLE_PROFILE.md` is thick enough to cover abstract shape, section/page allocation, figure/table inventory, related-work shape, evaluation layout, formatting/layout lessons, writing lessons, transfer plan, and no-prose-copy policy.
   - Confirm the final draft wrote `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md`, `paper/style_ref/STRUCTURE_CONFORMANCE.md`, and `paper/style_ref/STRUCTURE_CONFORMANCE.json`. The conformance JSON must map every final top-level section to an exemplar phase, evidence sources, applied exemplar lesson, and justified paper-specific deviation when needed; unmapped filler sections are hard blockers.
   - Confirm the exemplar is used only for structure and no prose, claims, examples, terminology, bibliography text, or figure design were copied.
   - Hard blockers: missing `research/LITERATURE_GROUNDING.json`, fewer than 10 recent papers, fewer than 3 classic anchors, missing trend-source metadata, missing `paper/style_ref/EXEMPLAR.json`, URL-only exemplar metadata without local PDFs/text/hash/profile evidence, missing structure blueprint/conformance, copied exemplar prose, or trend-only technical claims without evidence.

6. **citation audit**
   - Verify each `\cite{}` exists in the bibliography and is context-appropriate when web/local metadata are available.
   - Final-ready papers need bibliography depth, not just citation validity: at least 35 verified BibTeX entries, at least 30 unique cited keys in the paper source, and at least two rendered References pages before the Appendix when PDF text extraction is available.
   - Check citation distribution, not only counts: related-work citations should be grouped by method family, benchmark gap, failure mode, or claim topic, and each paragraph should cite the papers it actually discusses.
   - Treat citation dumping as a paper-quality blocker: one dense paragraph of all prior work, a mega-sentence of keys, citations hidden in captions, or unsupported prose followed by a citation pile is not acceptable. As a mechanical ceiling, any single citation command with more than eight keys or one paragraph with a large pile of unrelated keys must be rewritten into topic-specific prose.
   - Audit rendered bibliography quality, not only key existence: block `and 1 others`/`and N others`, BibTeX `author={... and others}` or `et al.` placeholders, missing venue metadata for published work, and title capitalization damaged by unbraced acronyms such as LLM, API-Bank, or EMNLP.
   - If web access is unavailable, mark citation checks `BLOCKED` or `WARN` with exact missing verification steps; do not fabricate metadata.
   - Hard blockers: invented citations, citation keys with no bibliography entry, too few verified/cited references, citation dumping, or claims that depend on unverifiable related work.

7. **fatal objection review**
   - Write the strongest concise rejection memo against the paper.
   - Adjudicate which points are already answered, which require writing fixes, and which require new experiments.
   - Hard blockers: a still-valid rejection that can be fixed within the current evidence but remains unaddressed.

8. **paper-quality calibration**
   - Write `paper/PAPER_QUALITY_CALIBRATION.md` and `paper/PAPER_QUALITY_CALIBRATION.json`.
   - Compare the candidate against the negative fresh-demo pilot pattern by failure signals, not by path: `baseline_not_beaten`, `synthetic_only_benchmark`, `underpowered_pilot` (small, single-source, or same-family-only evidence), `parser_or_schema_confound`, and `draft_self_reports_not_submission_quality`.
   - Extract only quality signals from positive exemplars such as EMNLP 2025 award papers: clear problem relevance, nontrivial contribution, public/resource-scale validation, strong baselines, and claim scope that matches measurement.
   - Confirm that benchmark selection was part of the literature survey: the project should cite recent/frontier, widely used public benchmarks and official repos considered for the domain before claiming that a synthetic benchmark is appropriate.
   - Confirm that the selected benchmark evidence is not single-source or planned-only: `experiments/BENCHMARK_PROVENANCE.md`/`.json` must list at least 3 independent executed practical/frontier benchmark sources/components as a hard minimum. Each selected source needs URL/repo, paper/citation/DOI, version/date, split/filtering, unique task count, license/access, capability/failure mode covered, selection rationale, and raw scored-row evidence. Planned diagnostic rows do not count.
   - Hard blockers: PASS/WARN while matching a hard negative pattern, failing to beat a nontrivial baseline for a comparative claim, synthetic-only pilot evidence presented as EMNLP-ready, any final claim based on an incomplete executed multi-source matrix, duplicated benchmark expansion, missing frontier/public benchmark survey, fewer than 3 executed real benchmark source families, single-source or same-family-only benchmark evidence, incomplete selected benchmark source provenance, or unresolved parser/schema confounds.

9. **research.md format preflight**
   - Invoke the EMNLP Format Preflight skill after the final compile and before model/vision review.
   - Run `python -m argus_skill.skills.pipeline_contracts validate-research-md-format --project-root .`; treat every issue as a blocking package defect.
   - Check `paper/FORMAT_PREFLIGHT.md` for the compile command, page count, conclusion page, figure/table inventory, bibliography status, fixes applied, and exact final validator result.
   - Hard blockers: missing or stale preflight, non-anonymous review author block, visibly underfilled main body, references after appendix, too few verified/cited references or too-short rendered References section, missing Limitations/Ethical Considerations, no reproducibility appendix, `[?]`, undefined references/citations, `Overfull \hbox > 5pt`, placeholders, `% UNVERIFIED`, unreferenced figures, excessive body figures, missing numerical table captions, missing paired-significance evidence, or missing `research.md` table-style tokens.
   - For underfilled-body or early-References blockers, decide whether the root cause is incomplete evidence. If yes, set `next_action: run_more_experiments` and name the exact missing benchmark condition, ablation, robustness/public-validation slice, or failure analysis. Only set `next_action: revise_paper` when the required evidence already exists and the repair is section organization, citation placement, or float/page flow.

10. **academic-language review**
   - Run `python -m argus_skill.skills.academic_language_review --project-root . --review-mode model --write` after prose stabilizes, then run `python -m argus_skill.skills.pipeline_contracts validate-academic-language-review --project-root .`.
   - Check `paper/ACADEMIC_LANGUAGE_REVIEW.json` for `score_1_to_5 >= 4`, `verdict: PASS`, `needs_revision: false`, no blocking issues, fresh hashes for all transitive LaTeX sources, model-backed review method, and quoted evidence spans from current source.
   - Hard-block final readiness if the body does not tell a reader what evaluated agent system was run: framework/runtime or benchmark harness, LLM/model identifiers/routes for evaluated agent calls, controller/skill/memory mechanism, task source, baselines, metrics, and budget/stopping rules must be visible in Method or Experimental Setup. For no-GPU final agent experiments, use and report the approved hosted route such as `gpt-5-mini`; if the benchmark loop is deterministic/no-external-model, downgrade it to a deterministic baseline or pilot instead of presenting it as final agent-system evidence. Do not count Argus/Codex daemon, engineer/reviewer routing, academic-language/layout review, paper-generation image-tool details, or orchestration/reviewer model names such as `gpt-5.4` / `gpt-5.4-mini` as method reproducibility facts.
   - Hard-block final readiness if the body lacks a professional cross-benchmark results matrix covering the selected 3+ benchmark/source families and major baselines/methods. The matrix must expose benchmark/source, task count/split, evaluated model/backend, metric, budget/decoding, and key result columns; otherwise reviewers cannot tell whether the claimed evidence is actually multi-source.
   - Hard-block final readiness if the paper has a stub abstract/introduction/method/setup: abstract should be 170--220 words, Introduction at least 900 words/about one full first page with at least three cited prior-work/benchmark hooks, Method at least 700 words, and Experimental Setup at least 550 words. These sections must contain reader-facing scientific exposition, not validator vocabulary, repeated contrastive templates, or repetitive caveats.
   - Hard-block final readiness if any result ratio/percentage in the manuscript cannot be regenerated from canonical summaries, or if the paper claims no external model calls while experiment metadata contains a hosted/model-backed method. This is an evidence-alignment failure, not a copy-editing issue.
   - Treat review artifacts as evidence, not targets. Do not hand-edit review JSON/Markdown to satisfy a gate; fix the manuscript/source artifacts and rerun the review so the underlying condition is independently true.
   - Hard blockers: missing or stale academic-language review, heuristic-only self-score, score below threshold, active revision directives, generic LLM-boilerplate opening, result-first or validator-shaped abstract, uncalibrated hype, missing What/Why/So-What contribution framing, claims not aligned to evidence, chronological related-work dump, or absent limitation scope.

11. **paper infrastructure review**
   - Run `python -m argus_skill.skills.paper_infrastructure_review --project-root . --review-mode model --write` after Method/Setup/caption/table/appendix prose stabilizes, then run `python -m argus_skill.skills.pipeline_contracts validate-paper-infrastructure-review --project-root .`.
   - Check `paper/PAPER_INFRASTRUCTURE_REVIEW.json` for `score_1_to_5 >= 4`, `verdict: PASS`, `needs_revision: false`, `leak_free: true`, no blocking/major issues, no active revision directives, fresh hashes for all transitive LaTeX sources, model-backed review method, and quoted evidence spans from current source.
   - Hard-block final readiness if title, abstract, body, captions, tables, or appendix prose exposes local hardware ordinals, CUDA/device variables, cache paths, local filesystem paths, API/private endpoint configuration, Argus/Codex daemon details, engineer/reviewer/critic/scientist routes, validation artifacts, review artifacts, image-tool plumbing, capability-vault configuration, or authoring/review model routes. These are local pipeline facts, not paper method facts.
   - Allow paper-facing evaluated-system facts such as evaluated model/backend, benchmark harness, task count/split, metric, decoding/budget setting, public benchmark version/date, and high-level compute budget when they describe the research system rather than the local machine.
   - Treat infrastructure review artifacts as evidence, not targets. Do not hand-edit `paper/PAPER_INFRASTRUCTURE_REVIEW.*`; fix rendered manuscript prose and rerun the tool.
   - Hard blockers: missing or stale paper infrastructure review, non-model self-score, score below threshold, `leak_free: false`, active directives, local environment/device/cache/path text in rendered prose, or nested `model_review` contradicting a top-level PASS.

12. **layout aesthetic review**
   - Run `python -m argus_skill.skills.paper_layout_review --project-root . --review-mode vision --write` after the final PDF compile, then run `python -m argus_skill.skills.pipeline_contracts validate-layout-review --project-root .`.
   - Check `paper/LAYOUT_REVIEW.json` for `score_1_to_5 >= 4`, `verdict: PASS`, `needs_revision: false`, no blocking issues, fresh PDF/page snapshot hashes, and a vision-based review method.
   - Hard blockers: missing or stale layout review, heuristic-only self-score, score below threshold, active revision directives, float/table dump pages, unreadable tiny table fonts, awkward whitespace, table/body overlap, `Overfull \hbox > 5pt`, more than five body figures, multiple `figure*` floats, square `1024x1024` conceptual figures, or image/caption layout that would make a reviewer reject the paper before reading.

13. **submission package**
   - Check official ACL/EMNLP style usage, anonymity, page budget, compile status, figures/tables existence, artifact manifest, and reproducibility notes.
   - Run `validate-full-scale-evidence`, `validate-image2-figures`, `validate-research-md-format`, `validate-academic-language-review`, `validate-paper-infrastructure-review`, `validate-layout-review`, `validate-paper-contract`, and the final `validate-full-emnlp` gate; block if the draft is a pilot/short/workshop scope, has an incomplete executed multi-source matrix for any required method/baseline condition, duplicates/relabels pilot episodes to inflate benchmark scale, lacks a literature-grounded survey of frontier/public benchmarks, relies on a single selected benchmark source or same-family variants only, omits source provenance for selected benchmarks, has main/body content outside the 7.5--8 page EMNLP long-paper range, starts References/Appendix before page 9, lacks official ACL style, lacks a core image-2 conceptual figure, lacks a passing format preflight, lacks a passing academic-language review, lacks a passing paper infrastructure review, lacks a passing visual layout review, or has no submission-stage ready/done state. Do not block on total page count after References/Appendix begin.
   - Also block if the evidence has fewer than 3 executed selected benchmark sources; planned diagnostic rows do not count.
   - Treat `missing_full_scale_experiment_run`, `incomplete_full_scale_experiment_run`, `missing_baseline_condition_run`, and `pilot_pdf_without_full_scale_evidence` as hard blockers. A compiled PDF or polished paper must remain `FAIL`/`BLOCKED` while any of these issue codes are present.
   - Validate `paper/VALIDATION_PRIORITY_POLICY.json` includes `experiment_evidence`, `content_sufficiency`, and `format_layout`. If these routes are missing, final assurance must fail because the daemon has no reliable path from short/weird papers back to experiments and evidence-backed analysis.
   - Enforce the full `research.md` preflight checklist before any `PASS`: no undefined references/citation warnings, no `[?]`, no `Overfull \hbox > 5pt`, body conclusion at or before page 8, References/Appendix not before page 9 when PDF text can be extracted, Limitations and Ethical Considerations present, References before Appendix, no total-page maximum after References/Appendix begin, ACL/EMNLP author-year citations with no numeric natbib override, at least 35 verified BibTeX entries, at least 30 unique cited keys, at least two rendered References pages when PDF text extraction is available, every BibTeX entry has author/editor/organization metadata and key-title-source consistency, every figure labeled and referenced, every table caption with a numerical headline, no placeholders, anonymous `Anonymous EMNLP Submission` author block, no `% UNVERIFIED` bibliography entries unless the operator has been told, at least one figure/table on each of pages 4--7, at least one paired-significance table when applicable, and complete reproducibility appendix.
   - Enforce table/figure style readiness: tables use `\footnotesize`, `\tabcolsep=3-4pt`, `\arraystretch=1.15`, light-gray header, soft peach "ours" row, alternating row tint, and bold winning values; every non-data figure is an adaptive/landscape page-width image-2 raster asset, preferably `1536x1024 or 1920x1080`, with no weird fonts, tiny text, heavy gradients, photorealism, or snake_case/code labels, and no self-drawn matplotlib/TikZ/SVG/PIL/HTML/PDF redraw substitution. The image-2 manifest must include raw generation `sidecar_path`, `inspect_path`, model-backed `review_path`, prompt/output SHA-256, dimensions, and API/tool evidence; manual-only review or hand-filled metadata is a blocker.
   - Verify that `paper/submission/` copies, if present, are listed in `paper/ARTIFACT_MANIFEST.json` and have fresh digests after the final copy step.
   - Environment blockers such as missing LaTeX or web access should be recorded as `BLOCKED`, not hidden behind a success-shaped fallback.

## Outputs
Write `paper/SUBMISSION_ASSURANCE.md` for humans and `paper/SUBMISSION_ASSURANCE.json` for machine checks.

The JSON must follow this shape:

```json
{
  "verdict": "FAIL",
  "blocking_issues": [
    {"layer": "paper_claim_audit", "issue": "unsupported numeric claim", "required_fix": "remove or cite result row"}
  ],
  "layers": {
    "experiment_integrity": {"verdict": "PASS", "evidence": ["experiments/run/manifest.json"]},
    "result_to_claim": {"verdict": "WARN", "evidence": ["paper/artifacts/claims_evidence.tsv"]},
    "paper_claim_audit": {"verdict": "FAIL", "evidence": ["paper/main.tex"]},
    "idea_provenance_and_code_reuse": {"verdict": "PASS", "evidence": ["research/IDEA_PROVENANCE.json", "research/CODE_REUSE_PLAN.json"]},
    "literature_and_exemplar_grounding": {"verdict": "PASS", "evidence": ["research/LITERATURE_GROUNDING.json", "paper/style_ref/EXEMPLAR.json"]},
    "citation_audit": {"verdict": "BLOCKED", "evidence": ["paper/references.bib"]},
    "fatal_objection_review": {"verdict": "WARN", "evidence": ["paper/FATAL_OBJECTION_REVIEW.md"]},
    "paper_quality_calibration": {"verdict": "FAIL", "evidence": ["paper/PAPER_QUALITY_CALIBRATION.json"]},
    "research_md_format_preflight": {"verdict": "FAIL", "evidence": ["paper/FORMAT_PREFLIGHT.md"]},
    "academic_language_review": {"verdict": "FAIL", "evidence": ["paper/ACADEMIC_LANGUAGE_REVIEW.json"]},
    "paper_infrastructure_review": {"verdict": "FAIL", "evidence": ["paper/PAPER_INFRASTRUCTURE_REVIEW.json"]},
    "layout_aesthetic_review": {"verdict": "FAIL", "evidence": ["paper/LAYOUT_REVIEW.json"]},
    "submission_package": {"verdict": "PASS", "evidence": ["paper/main.pdf"]}
  },
  "next_action": "revise_paper | run_more_experiments | verify_citations | ready_to_submit | pivot"
}
```

The calibration JSON must include explicit machine-checkable signals:

```json
{
  "verdict": "FAIL",
  "quality_signals": {
    "uses_public_benchmark": false,
    "beats_nontrivial_baseline": false,
    "n_tasks_meets_threshold": false,
    "parser_schema_confound_cleared": false,
    "submission_quality_self_assessment": "pilot"
  },
  "negative_case_regressions": [
    {
      "case_id": "negative:fresh-demo-pilot-pattern",
      "matched": true,
      "hard_failure": true,
      "signals": ["baseline_not_beaten", "synthetic_only_benchmark", "parser_or_schema_confound"]
    }
  ],
  "quality_signals_from_positive_examples": [
    {
      "case_id": "positive:emnlp2025-best-infini-gram-mini",
      "signals_used": ["clear_problem_with_broad_relevance", "scale_or_resource_evidence"],
      "source_url": "https://2025.emnlp.org/program/awards/"
    }
  ],
  "blocking_issues": [
    {"issue": "pilot evidence does not beat baseline", "required_fix": "pivot or run stronger validation"}
  ]
}
```

## Decision rules
- Overall `PASS`: all hard layers pass and `blocking_issues` is empty.
- Overall `WARN`: all listed validators and hard gates pass, and only non-blocking caveats outside those gates remain; otherwise use `FAIL` or `BLOCKED`. Environment-limited verification can be `WARN` only for an explicitly non-final/operator-accepted scope. Pilot scale, same-family-only evidence, or any item listed above as a hard blocker is never a `WARN` for final EMNLP readiness; it is `FAIL`, `BLOCKED`, or a non-final operator-accepted scope.
- Overall `FAIL`: the draft makes claims that the evidence does not support or has fixable paper defects.
- Overall `BLOCKED`: required external resources are unavailable; record exact unblock steps.
- Overall `ERROR`: the audit could not complete because required files are malformed.
- Never emit `PASS` or `WARN` if `validate-manifest` reports any issue. Stale generated artifacts are evidence-integrity blockers, not formatting warnings.
- Never emit `PASS` or `WARN` if `validate-grounding`, `validate-idea-provenance`, `validate-code-reuse`, `validate-exemplar`, `validate-image2-figures`, `validate-research-md-format`, `validate-academic-language-review`, `validate-paper-infrastructure-review`, `validate-layout-review`, `validate-paper-contract`, or `validate-full-emnlp` reports any issue. A project without literature/news/classic grounding, a literature-derived idea, a license-aware code survey, downloaded/thick top-paper exemplar learning, image-2 conceptual figure, passing `research.md` format preflight, passing academic-language review, passing paper infrastructure review, passing visual layout review, complete EMNLP long-paper scope, and submission-stage readiness is not submission-ready.

## Response shape
- State the overall verdict and the highest-severity blocker.
- List the exact artifact paths written.
- If not `PASS`, name the next stage: revision, more experiments, citation verification, or pivot.
