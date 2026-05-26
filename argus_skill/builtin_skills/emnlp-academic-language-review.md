---
name: EMNLP Academic Language Review
description: Score and revise an EMNLP/ACL paper for academic prose, narrative framing, and claim calibration before final layout review.
category: paper-review
version: 1
scientist_model: gpt-5.4
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
- `python -m argus_skill.skills.pipeline_contracts validate-full-scale-evidence --project-root .` reports `missing_full_scale_experiment_run`, `incomplete_full_scale_experiment_run`, `missing_baseline_condition_run`, or `pilot_pdf_without_full_scale_evidence`; fix/run the full evidence matrix before final prose polish.

## How to solve
1. Read the evidence before editing prose:
   - `research/NARRATIVE_REPORT.md`
   - `research/IDEA_PROVENANCE.json`
   - `research/LITERATURE_GROUNDING.json`
   - `paper/RESULTS_REPORT.md`
   - `paper/artifacts/result_to_claim.tsv`
   - `paper/PAPER_QUALITY_CALIBRATION.json`
   - Run `python -m argus_skill.skills.pipeline_contracts validate-full-scale-evidence --project-root .` for any final EMNLP/ACL paper. Do not accept benchmark construction, `benchmarks/full/manifest.json`, or `status.json task_count` as execution evidence; final language must be grounded in raw completed scored `experiments/**` rows for every required method/baseline condition.

2. Rebuild the paper story:
   - Write one thesis sentence in the form: "X is better for Y in Z because W."
   - State the contribution as: "We propose X. We show X improves Y by Z because W."
   - Make every main section answer What, Why, and So What.
   - Use an inner/outer loop: check each experiment claim locally, then synthesize what pattern it supports globally.

3. Fix the abstract and introduction:
   - Abstract should be about five evidence-backed sentences: problem, gap, method, result, implication.
   - Keep the abstract reader-facing. Do not satisfy evidence alignment by inserting appendix/figure/table references, raw artifact paths, validator/review-gate vocabulary, evidence-span quotes, or `% evidence:` comments inside the abstract environment.
   - Do not start the abstract with a numeric win. The first sentence should establish the concrete problem or evaluation gap; the result should come after the method is named.
   - Calibrate without sounding defensive: one scoped phrase is fine, but repeated "controlled/synthetic/benchmark-scoped/not causal proof" caveats belong in limitations or discussion.
   - Do not open with generic phrases such as "Large language models have achieved remarkable success" or "In recent years..."
   - Introduction should move from concrete problem to literature gap, method insight, quantified result, and contribution list.

4. Fix related work and positioning:
   - Group related work by method, benchmark, or failure mode; do not write a chronological list.
   - Cite papers next to the claim or paragraph that discusses them. Do not stack all citations in one dense paragraph, one mega-sentence, a caption, or a detached bibliography dump; split any citation command above eight keys into topic-specific sentences.
   - Each group should end with the exact gap this paper addresses.
   - Use only verified citations with full author metadata. If a citation cannot be verified, mark it as blocked instead of inventing metadata; do not leave BibTeX `author={... and others}`/`et al.` placeholders that render as `and 1 others`.

5. Calibrate claims:
   - Remove SOTA, novel, significant, robust, or generalization claims unless local evidence and citations support them.
   - Every numeric result in prose, table captions, and figure captions must trace to a local artifact.
   - Captions should state the takeaway, not only describe the figure.

6. Replace agent-looking prose:
   - Remove filler, boilerplate, and repeated "we demonstrate" sentences.
   - Use human-readable method and baseline names in paper-facing text.
   - Keep raw identifiers, file paths, and snake_case labels in comments, manifests, or appendices only.
   - Do not leave paper-facing format artifacts that read like agent output: placeholders, `% UNVERIFIED` citations, unresolved `[?]` references, code-like section/table/figure labels, or captions that describe a plot without a numerical takeaway.
   - Caption prose must support the `research.md` format contract: every table caption has a numerical headline, every figure caption states an evidence-backed takeaway, and any paired-significance claim is backed by a local artifact.

7. Run the tool-backed review:
   - Run `python -m argus_skill.skills.academic_language_review --project-root . --review-mode model --write`.
   - Then run `python -m argus_skill.skills.pipeline_contracts validate-academic-language-review --project-root .`.
   - The review must write `paper/ACADEMIC_LANGUAGE_REVIEW.json`, `paper/ACADEMIC_LANGUAGE_REVIEW.md`, and history.
   - Passing requires a model-backed review, fresh hashes for all LaTeX sources included by `paper/main.tex`, score at least 4/5, evidence spans quoted from the source, no failed required checks, and no active revision directives. Evidence spans are review artifacts, not prose: do not paste them into the paper to appease the gate.

8. Iterate:
   - Apply `revision_directives` exactly: rewrite abstract, tighten contribution sentence, calibrate claims, reorganize related work, add evidence sentences, replace hype language, or add limitation scope.
   - Re-run the review after every prose-changing edit.
   - Do not claim final readiness from a self-written score or heuristic-only review.

## Response shape
- State the academic-language score and whether `validate-academic-language-review` passed.
- Name the strongest rewritten contribution sentence.
- If blocked, quote the highest-priority revision directive and the source file it targets.
