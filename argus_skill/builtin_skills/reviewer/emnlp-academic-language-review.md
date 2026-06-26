---
name: EMNLP Academic Language Review
description: Score and revise an EMNLP/ACL paper for academic prose, narrative framing, and claim calibration before final layout review.
category: paper-review
version: 1
created_at: 2026-05-25T00:00:00+00:00
---

## Title
EMNLP Academic Language Review

## Description
Run the final narrative/prose gate for an EMNLP-style paper. This skill adapts workflow concepts from the MIT-licensed `AI-Research-SKILLs` repository--inner/outer research synthesis, What/Why/So-What framing, paragraph-level paper planning, and rigor review--without copying exemplar prose.

## When to use
- `paper/main.tex` exists and the paper is intended to be EMNLP/ACL submission quality.
- The operator says the academic language, story, related work, or contribution framing is weak.
- The pipeline is between paper drafting/revision and final visual layout assurance.

## When NOT to use
- Experiment evidence is missing; run analysis or benchmarks first.
- The paper is only a pilot note and should not be polished into a fake long paper.
- The task is purely visual layout; use the layout review after language changes are done.
- the full-scale experiment-evidence requirement is not satisfied and review reports `missing_full_scale_experiment_run`, `incomplete_full_scale_experiment_run`, `missing_baseline_condition_run`, or `pilot_pdf_without_full_scale_evidence`; fix/run the full evidence matrix before final prose polish.

## How to solve
1. Read the evidence before editing prose:
   - `research/NARRATIVE_REPORT.md`
   - `research/LITERATURE_GROUNDING.json`
   - `paper/RESULTS_REPORT.md`
   - `paper/artifacts/result_to_claim.tsv`
   - `paper/PAPER_QUALITY_CALIBRATION.json`
   - Self-audit the full-scale experiment-evidence requirement (completed raw scored rows under `experiments/**` for every required method/baseline condition) for any final EMNLP/ACL paper. Do not accept benchmark construction, `benchmarks/full/manifest.json`, or `status.json task_count` as execution evidence; final language must be grounded in raw completed scored `experiments/**` rows for every required method/baseline condition.

2. Rebuild the paper story:
   - Write one thesis sentence in the form: "X is better for Y in Z because W."
   - State the contribution as: "We propose X. We show X improves Y by Z because W."
   - If the current ablations do not isolate `W`, do not keep defending a mechanism claim. Reset the thesis to the measured comparison: "On benchmark/task slice Z, X reaches Y compared with baseline B under protocol P; which subcomponent causes the gain remains unresolved." Move the unresolved mechanism discussion to analysis or limitations.
   - Make every main section answer What, Why, and So What.
   - Use an inner/outer loop: check each experiment claim locally, then synthesize what pattern it supports globally.

3. Fix the abstract and introduction:
   - Abstract should be about five evidence-backed sentences and normally 170--220 words: problem, gap, method, evaluated model/benchmark mix, result, implication.
   - Keep the abstract reader-facing. Do not satisfy evidence alignment by inserting appendix/figure/table references, raw artifact paths, validator/review-gate vocabulary, evidence-span quotes, or `% evidence:` comments inside the abstract environment.
   - Do not start the abstract with a numeric win. The first sentence should establish the concrete problem or evaluation gap; the result should come after the method is named.
   - Calibrate without sounding defensive: one scoped phrase is fine, but repeated "controlled/synthetic/benchmark-scoped/not causal proof" caveats belong in limitations or discussion.
   - Do not open with generic phrases such as "Large language models have achieved remarkable success" or "In recent years..."
   - Introduction should move from concrete problem to cited literature/benchmark gap, method insight, quantified result, and contribution list. Treat introduction length as a signal, not an automatic rule: reject an opening when it is missing those functions, reads like compressed validator prose, or fails to use the long-paper body budget, but do not reject solely because a word counter is below a fixed target. Also reject introductions with fewer than three citations before Related Work: the first page must situate the problem with verified prior work or benchmark papers, not just project-local motivation.
   - Reject stale-evidence prose: if a result ratio or percentage appears near a method/control name, it must be traceable to the latest canonical summary/table artifacts. Also reject contradictions where one section claims "no external LLM/model calls" while setup, manifests, or tables report a hosted/model-backed baseline.

4. Fix related work and positioning:
   - Group related work by method, benchmark, or failure mode; do not write a chronological list.
   - Cite papers next to the claim or paragraph that discusses them. Do not stack all citations in one dense paragraph, one mega-sentence, a caption, or a detached bibliography dump; split any citation command above eight keys into topic-specific sentences.
   - Each group should end with the exact gap this paper addresses.
   - Use only verified citations with full author metadata and ACL/EMNLP author-year natbib style. If a citation cannot be verified, mark it as blocked instead of inventing metadata; do not leave BibTeX `author={... and others}`/`et al.` placeholders that render as `and 1 others`, do not leave title-only entries from missing authors, and do not use starter keys whose titles point to unrelated papers.

5. Calibrate claims:
   - Remove SOTA, novel, significant, robust, or generalization claims unless local evidence and citations support them.
   - Every numeric result in prose, table captions, and figure captions must trace to a local artifact.
   - Captions should state the takeaway, not only describe the figure.
   - When `ACADEMIC_LANGUAGE_REVIEW.json` says a headline mechanism claim is unsupported or not isolated, apply a claim-scope reset instead of another adjective pass: remove mechanism nouns from title, abstract, opening contribution, and conclusion; choose one exact reader-facing headline result; add one quantified sentence with method, comparator, task slice, n, and metric; state the unisolated submechanism once in limitations.
   - Acceptable evidence sentence pattern: "In the N-task T slice, X achieves A on metric M versus B for comparator C under protocol P." This satisfies evidence alignment without claiming why X works.

6. Enforce method/system readability:
   - The Method and Experimental Setup must be readable without internal project context. They must name the evaluated paper system, its paper-facing framework, benchmark harness, or controller; the controller/skill/memory mechanism; task source/version; baselines; metrics; evaluated model/backend; and budget/stopping rules. For hosted agent experiments, the final paper should name the paper-facing hosted backbone such as `gpt-5-mini` plus decoding/settings/budget. For scorer-based experiments, name the evaluated scorer/backend such as `PairScorer` and describe the candidate-ranking protocol without adding authoring-environment details. Do not treat Argus, Codex engineer/reviewer routes, daemon handoff, academic-language/layout review, image-tool infrastructure, or orchestration/reviewer model names such as `gpt-5.5` / `gpt-5.5-mini` as paper-method details.
   - Add a compact system/configuration table when prose alone would be ambiguous. The table must be professional and paper-facing: benchmark/component name, task count/split, evaluated model/backend, method or baseline role, runtime/harness, metric, budget/decoding, and the numerical takeaway. It must not expose Argus/Codex route names, engineer/reviewer/author roles, `gpt-5.5*`, API keys, private endpoints, capability-vault contents, or validation artifacts.
   - Require the Results section to contain a large, reader-facing cross-benchmark matrix for final long-paper claims. The table should let a reviewer compare the selected 3+ benchmark/source families, evaluated model/backend, task counts/splits, major baselines/methods, metrics, budget/decoding, and key scores in one place. Reject disconnected tiny tables when they hide missing benchmarks or omit model/backend information.
   - Reject papers that only say "our agent" or "the system" while omitting what framework ran it, which model powered it, and how one benchmark episode executes.
   - For local environment/device/cache/path leakage, delegate the final judgment to the dedicated model-backed paper infrastructure review: run `python -m argus_skill.verticals.research.paper_infrastructure_review --project-root . --review-mode model --write` and self-audit the paper-infrastructure review thresholds (leak_free, score). Do not patch this by adding local regex filters to the academic-language skill.

7. Replace agent-looking prose:
   - Remove filler, boilerplate, repeated "we demonstrate" sentences, repeated not-X-but-Y transitions, and over-defensive "narrow / benchmark-scoped / not general" caveats. One calibrated scope statement is enough; the rest belongs in limitations.
   - Use human-readable method and baseline names in paper-facing text.
   - Keep raw identifiers, file paths, and snake_case labels in comments, manifests, or appendices only.
   - Do not leave paper-facing format artifacts that read like agent output: placeholders, `% UNVERIFIED` citations, unresolved `[?]` references, code-like section/table/figure labels, or captions that describe a plot without a numerical takeaway.
   - Caption prose must support the `research.md` format contract: every table caption has a numerical headline, every figure caption states an evidence-backed takeaway, and any paired-significance claim is backed by a local artifact.

8. Run the tool-backed review:
   - Run `python -m argus_skill.verticals.research.academic_language_review --project-root . --review-mode model --write`.
   - Then self-audit the academic-language review thresholds; the L2 reviewer verifies the review artifact directly against the review stage checklist.
   - The review must write `paper/ACADEMIC_LANGUAGE_REVIEW.json`, `paper/ACADEMIC_LANGUAGE_REVIEW.md`, and history.
   - Passing requires a model-backed review, fresh hashes for all LaTeX sources included by `paper/main.tex`, score at least 4/5, evidence spans quoted from the source, no failed required checks, and no active revision directives. Evidence spans are review artifacts, not prose: do not paste them into the paper to appease the gate.
   - Treat `paper/ACADEMIC_LANGUAGE_REVIEW.json`, `.md`, and `_history.jsonl` as generated evidence, not editable scoring targets. Do not hand-edit, normalize, or append a top-level `PASS`; if the nested `model_review` still says revise, lists major/blocking issues, failed checks, low section scores, or revision directives, the only valid repair is to revise the manuscript and rerun the review tool.

9. Iterate:
   - Apply `revision_directives` exactly: rewrite abstract, tighten contribution sentence, calibrate claims, reorganize related work, add evidence sentences, replace hype language, or add limitation scope.
   - Re-run the review after every prose-changing edit.
   - Do not claim final readiness from a self-written score or heuristic-only review.
   - If three academic-language rounds fail with the same unsupported-headline or missing-quantified-claim blocker and the paper already has enough evidence/pages, stop local wording churn and perform the claim-scope reset above before the next review.
   - If two consecutive model-backed academic-language rounds remain below 4/5 or keep reporting `salesy_novel_language`, `calibrated_no_hype=false`, repeated score restatements, contrastive templates, or generic labels such as "proposed method", stop lexical search-and-replace. Read the full `paper/ACADEMIC_LANGUAGE_REVIEW.json`, record the failing required check, score, evidence spans, and directive text, then perform a paragraph-level prose reset.
   - Paragraph-level prose reset means rewriting the full abstract, the full Introduction, the contribution paragraph, metrics paragraph, main-results lead, analysis lead, limitations, and conclusion around natural scientific exposition. Use the concrete system/method name, not "proposed method"; use at most one numeric headline sentence per paragraph; let tables carry repeated scores; keep one explicit method-comparator-task-slice sentence; and replace repeated "X matters because", "beats", "only", "strongest", "compact/readable/clear highlight", and score-restatement templates with direct protocol/result statements.
   - A low-score style loop is not solved by making the paper shorter. Before and after the paragraph-level prose reset, check `paper/PAPER_DRAFT_REPORT.json` or the generated page-budget artifact. Preserve 7.5--8 main-content pages, keep the conclusion in the body, and keep References starting after the eight-page body. If the reset removes more than about a quarter page or drops below the page target, add source-backed method detail, evaluation protocol detail, failure analysis, robustness/public-validation evidence, or limitation analysis before rerunning the review; do not add filler.
   - The desired prose is normal reviewer-facing EMNLP writing, not a lab notebook. Keep explanatory connective tissue and section flow while removing overclaiming; do not strip every adjective if the sentence is already factual and supported.

## Response shape
- State the academic-language score and whether the academic-language review thresholds hold.
- Name the strongest rewritten contribution sentence.
- If blocked, quote the highest-priority revision directive and the source file it targets.
