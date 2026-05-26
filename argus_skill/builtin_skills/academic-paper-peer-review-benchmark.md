---
name: Academic Paper Peer Review Benchmark
description: Simulate a strict EMNLP/ACL-style program-committee reviewer for a nearly complete academic paper, scoring contribution, evidence, experiments, writing, format, and readiness before reviewer agents accept paper tasks as done.
category: paper-review
version: 1
scientist_model: gpt-5.4
created_at: 2026-05-25T00:00:00+00:00
---

## Title
Academic Paper Peer Review Benchmark

## Description
Use this as the reviewer agent's built-in benchmark when the task is an academic-paper mission and the paper is already fairly complete: `paper/main.tex` exists, a compiled PDF or draft report exists, experiments/results have been written into the paper, or the current scope is `final_submission`. It distills ARIS-style paper audit loops, `research.md` EMNLP preflight rules, ARA rigor-review dimensions, and the Auto Research Pipeline final contract into one simulated peer-review rubric.

This skill is not a copyediting pass. It is a calibrated reject/accept simulation that decides whether a real reviewer would still reject the paper and turns that judgment into a concise engineer handoff.

## When to use
- The reviewer is judging a paper drafting, paper revision, submission assurance, or final submission task.
- The project has a manuscript (`paper/main.tex`, `paper/main.pdf`, or equivalent) plus claims, results, figures/tables, and bibliography.
- The main agent asks to call the paper "complete", "EMNLP-ready", "submission-ready", or "publication quality".
- `validate-full-emnlp`, submission assurance, academic-language review, layout review, or format preflight output is available.

## When NOT to use
- The task is only literature search, experiment planning, benchmark implementation, or early smoke testing with no complete draft.
- The operator asks for a narrow code fix unrelated to a manuscript.
- The evidence is missing so completely that the right action is to run experiments, not simulate final peer review.

## Reviewer stance
- Review like a skeptical EMNLP/ACL PC reviewer, not like the author.
- Prefer `continue` over `done` if any major reviewer objection remains actionable.
- Do not reward a PDF merely for existing. A PDF with weak evidence, copied benchmarks, stale artifacts, or underfilled body pages is a reject.
- Every weakness must include a concrete fix. The reviewer handoff should be professional and short enough for a smaller engineer model to execute.
- Separate paper-quality issues from environment blockers. Missing web/LaTeX access can be `blocked`; weak experiments, stale manifests, bad prose, and format failures are `continue`.
- Treat review artifacts, calibration files, and readiness reports as evidence, not optimization targets. A paper passes only when the manuscript, raw artifacts, generated figures/tables, and validators independently satisfy the underlying condition.

## Required input artifacts to inspect or demand
- Manuscript: `paper/main.tex`, section files, `paper/main.pdf`, and `paper/main.log`.
- Paper contract: `paper/PAPER_DRAFT_REPORT.json`, `paper/PAGE_BUDGET.md`, `paper/TEMPLATE_SOURCE.md`, and `research/PIPELINE_STATE.json`.
- Evidence: `paper/artifacts/results_summary.tsv`, raw `experiments/**` and `results/**` records/logs, `paper/artifacts/claims_evidence.tsv`, and `paper/SUBMISSION_ASSURANCE.json`.
- Grounding: `research/LITERATURE_GROUNDING.json`, `research/IDEA_PROVENANCE.json`, `research/CODE_REUSE_PLAN.json`, `experiments/BENCHMARK_PROVENANCE.md` or `.json`.
- Style/review gates: `paper/FORMAT_PREFLIGHT.md`, `paper/ACADEMIC_LANGUAGE_REVIEW.json`, `paper/LAYOUT_REVIEW.json`, `paper/style_ref/EXEMPLAR.json`, and `paper/figures/IMAGE2_FIGURES.json`.

## Eight review dimensions
Score each dimension 1--5. Most plausible drafts should land in the 2--4 range; a 5 means genuinely conference-ready, not merely "no obvious syntax errors".

1. **Contribution and venue fit**
   - Is there a clear X-Y-Z-W claim: "We propose X. We show X improves Y by Z because W"?
   - Is the problem important to EMNLP/ACL reviewers, not just an internal demo?
   - Is novelty derived from literature, benchmark gaps, trend signals, and code sources rather than agent brainstorming?

2. **Claim-evidence alignment**
   - Does every abstract/introduction/result/conclusion claim cite a raw local artifact or table row?
   - Are numeric claims, superlatives, SOTA/generalization phrases, captions, and limitations consistent with the evidence?
   - Are unsupported claims removed or softened instead of hidden behind vague wording?

3. **Experiment and benchmark integrity**
   - Final evidence requires at least 240 unique semantic scored main tasks/episodes; 50/60-task runs are pilots.
   - Do not accept duplicated benchmark expansion: no copied, relabelled, shuffled, suffix-renamed (`_r2`, `_copy`, `_dup`) prompts/specs/gold answers.
   - Benchmark provenance must list selected benchmark sources/components, target 3+ independent real/frontier sources when feasible, and include at least 2 independent sources as a hard minimum.
   - Selected sources should be practical/frontier and not single-family: include URL/repo, paper/citation/DOI, version/date, split/filtering, task count, license/access, capability covered, and rationale.
   - Baselines must include meaningful nontrivial comparisons; positive comparative claims require beating the strongest relevant baseline with statistical support.
   - Ablations must isolate the proposed mechanism; metrics must match the claim.

4. **Literature, related work, and citation quality**
   - Literature grounding should include at least 10 recent high-quality papers and 3 classic anchors.
   - The bibliography should be deep enough for a long paper: at least 35 verified BibTeX entries, at least 30 unique cited keys, and no invented or `% UNVERIFIED` entries unless explicitly disclosed.
   - Related work should be methodological and contrastive, not a chronological list.

5. **Reproducibility and artifact audit**
   - Manifest digests, TSV schemas, generated artifacts, source links, and paper copies must be fresh.
   - Result tables and figures must be generated from canonical artifacts, not hand-edited numbers.
   - The appendix must include model IDs, prompts/cache fingerprints, seeds, hyperparameters, compute/cost, scoring scripts, and significance methodology.

6. **Writing quality and reviewer readability**
   - Abstract and introduction should quickly answer What, Why, So What, and why now.
   - The abstract must read like a normal EMNLP abstract: problem/gap first, method/result/implication after; no Appendix Figure/Table references, raw artifact paths, evidence-span quotes, validator vocabulary, or repeated defensive caveats.
   - The method should be re-implementable from the description.
   - Results should lead with supported takeaways, then caveats and failure analysis.
   - Limitations and ethics should be honest and specific, not boilerplate.

7. **Format, layout, and visual evidence**
   - Enforce `research.md`: official ACL/EMNLP review template, anonymous author block, 7.5--8 main-content pages, conclusion by page 8, Limitations/Ethics after conclusion, References before Appendix, and complete reproducibility appendix.
   - No unresolved references/citations, `[?]`, placeholders, or `Overfull \hbox > 5pt`.
   - Every figure is labeled and referenced; every table caption has a numerical headline; at least one figure/table appears on each of pages 4--7; paired-significance evidence appears when comparative binary outcomes are reported.
   - Figure 1, teaser, overall, and core method/framework/system/pipeline overview figures must use actual image-2/codex-image2 raster output in `paper/main.tex`. Hard reject self-drawn substitutes from matplotlib, TikZ node graphs, SVG/PIL/HTML canvas, screenshots, Inkscape/manual vectors, or cleaned PDFs.
   - Tables should use the research.md style tokens: `\footnotesize`, `\tabcolsep=3-4pt`, `\arraystretch=1.15`, light-gray header, soft peach "ours" row, alternating row tint, and bold winning values.

8. **Adversarial reviewer kill argument**
   - Write the strongest short reason to reject the paper.
   - If the reason is still valid and fixable, choose `continue`.
   - If the reason requires new experiments, say so directly; do not ask the engineer to paper over it with prose.

## Recommendation mapping
- **Strong Accept**: mean >= 4.5, no dimension below 4, no hard blockers.
- **Accept**: mean >= 4.0, no dimension below 3, no hard blockers.
- **Weak Accept**: mean >= 3.5, no dimension below 3, only minor fixable issues.
- **Weak Reject**: mean >= 2.5 or any dimension below 3, with at least one major weakness.
- **Reject**: mean < 2.5 or any critical flaw.

Hard blockers force **Reject** or **Weak Reject** regardless of mean: failed `validate-full-emnlp` for `final_submission`, under-240 final evidence, duplicated benchmark expansion, single-source benchmark evidence for broad effectiveness claims, missing nontrivial baseline for comparative claims, unsupported headline claim, stale manifest/digest, unresolved citations, self-drawn core overview figure, severe overfull boxes, underfilled long-paper body, or missing limitations/ethics.

## Reviewer output contract
When this skill applies, include a compact simulated-review section inside `round_summary_markdown`:

```markdown
### Simulated peer-review benchmark
- Recommendation: Weak Reject
- Scores: contribution 4, evidence 2, experiments 2, literature 3, reproducibility 3, writing 4, format/layout 2, kill-argument 2
- Strongest accept argument: ...
- Strongest reject argument: ...
- Blocking issues: ...
```

If `status` is `continue`, make `next_action` a concise engineer prompt:

1. List the top 1--3 blocking reviewer objections.
2. Name exact files/artifacts to repair.
3. Include exact validation commands, especially `python -m argus_skill.skills.pipeline_contracts validate-full-emnlp --project-root .` for final submission.
4. Tell the engineer not to claim completion until those commands pass.

If `status` is `done`, `completion_summary_markdown` must cite the passing gate output and explain why no simulated reviewer hard blocker remains.
