---
name: Paper Review Revision Loop
description: Review and revise a paper draft against EMNLP-style criteria, applying concrete fixes and rechecking claims, academic language, figures, layout, and compile status.
category: paper-review
version: 1
scientist_model: gpt-5.4
created_at: 2026-05-23T00:00:00+00:00
---

## Title
Paper Review Revision Loop

## Description
Improve an existing paper draft using an external-review mindset. This adapts ARIS auto-paper-improvement-loop, paper-claim-audit, and MIT-licensed `AI-Research-SKILLs` narrative/rigor review concepts into argus-skill's reviewer-gated loop: find critical weaknesses, patch the paper, recompile, and keep an audit trail.

## When to use
- `paper/main.tex` or a manuscript draft exists.
- The operator asks to improve, review, polish, or make the paper EMNLP-ready.
- Reviewer feedback, paper draft reports, or known weaknesses need to be converted into edits.

## When NOT to use
- No paper draft exists; use the paper drafting skill first.
- The task requires new experiments before any writing fix is meaningful; run experiments first or create a benchmark mission.
- The operator asks for purely stylistic copyediting with no technical review.

## How to solve
1. Snapshot the current draft:
   - Copy or record the current `paper/main.tex`, section files, figures, and compile status.
   - Do not overwrite prior submission directories.
   - If `paper/SUBMISSION_ASSURANCE.md` or `paper/SUBMISSION_ASSURANCE.json` exists, treat it as the primary blocker list and revise against it before lower-priority polish.

2. Review like an EMNLP reviewer:
   - Check problem motivation, novelty framing, method clarity, experimental validity, comparison fairness, reproducibility, limitations, and ethics.
   - Check claim/evidence alignment before style.
   - Check academic story quality: concrete problem gap, What/Why/So-What contribution, methodological related work, evidence-backed abstract, calibrated tone, and non-boilerplate opening.
   - Reject validator-shaped abstracts: no result-first numeric opening, appendix/figure/table references, raw artifact paths, evidence-span quotes, review-gate vocabulary, or long defensive caveat strings in the abstract.
   - Check the `research.md` formatting contract before cosmetic polishing: official ACL/EMNLP review template, anonymous author block (`Anonymous EMNLP Submission`), conclusion by page 8, Limitations and Ethical Considerations after the conclusion, References before Appendix, complete reproducibility appendix, no unresolved refs/citations, no `[?]`, no `% UNVERIFIED` bibliography entries, no placeholders, and no `Overfull \hbox > 5pt`.
   - Check figure/table constraints: every figure labeled and referenced, every table caption has a numerical headline, at least one figure/table on each of pages 4--7, at least one paired-significance table when comparisons are reported, <=5 body figures, only one `figure*`, no square `1024x1024` conceptual figure, and no code-like/snake_case labels in paper-facing visuals.
   - If the paper is short, weirdly sparse, or References start before page 9, do not treat it as a prose-polish task until evidence sufficiency is checked. Inspect `validate-full-scale-evidence`, `paper/EVIDENCE_GAPS.json`, `paper/CLAIM_GRAPH.json`, and raw result logs. If missing evidence is addressable, send the next repair to experiments/analysis, not layout.
   - Rank weaknesses as critical, major, or minor.

3. Write `paper/REVIEW_REPORT.md`:
   - Score estimate if useful.
   - Top weaknesses and exact minimum fixes.
   - Any missing evidence or citations.
   - Any claims that should be softened or removed.

4. Apply fixes:
   - Fix critical claim/evidence issues first.
   - Prefer conservative language over unsupported expansion.
   - Update captions, limitations, experiment setup, and result interpretation.
   - Do not add new numeric claims unless a local artifact supports them.
   - If `paper/ACADEMIC_LANGUAGE_REVIEW.json` exists and fails, treat its `revision_directives` as concrete prose tasks before layout polish. Allowed language fixes include rewriting the abstract/introduction, tightening the thesis sentence, replacing generic openings, reorganizing related work, adding evidence-aligned takeaway sentences, softening unsupported claims, adding limitation scope, and replacing raw code identifiers in body prose. Evidence alignment should be checked through artifacts and comments outside the abstract, not by stuffing audit text into the abstract.
   - If `paper/LAYOUT_REVIEW.json` exists and fails, treat its `revision_directives` as concrete layout tasks. Allowed layout fixes include splitting/merging/moving tables, shortening low-value sections, deleting filler, resizing/regenerating figures, rebalancing columns, fixing overfull boxes, and replacing code-like labels with human-readable paper labels.
   - For table fixes, prefer the `research.md` table tokens before shrinking content unreadably: `\footnotesize`, `\tabcolsep=3-4pt`, `\arraystretch=1.15`, light-gray header, soft peach "ours" row, alternating row tint, and split/appendix relocation when the table still causes overflow.
   - For Figure 1, teaser, overall, and core overview fixes, regenerate through image-2/codex-image2 with a better prompt and update `paper/figures/IMAGE2_FIGURES.json` plus `paper/main.tex` to use the new raster `output_path`. Prompt, provenance, generation-setting, raw generation sidecar, inspect sidecar, model-backed review, and accepted-attempt changes count as figure source changes. The replacement prompt must use the imported `research.md` scaffold: dense Figma-style paper figure, exact pinned labels, named layout variant from the canonical menu, explicit negative constraints, and Figma cleanup tokens. Do not draw, redraw, trace, clean, or "improve" these conceptual overview figures yourself with matplotlib/FancyBboxPatch, TikZ, SVG/PIL/HTML canvas, Inkscape/manual vectors, screenshots, cleaned PDF derivatives, or hand-filled image-2 metadata around a local PNG.
   - For non-overview figure fixes, keep conceptual figures adaptive/landscape page-width assets, preferably `1536x1024 or 1920x1080`, with clean Figma-style rounded cards, readable labels, and no decorative gradients, photorealism, sketchy/weird fonts, or square `1024x1024` canvas.

5. Recheck artifacts:
   - Run claim/evidence audit steps.
   - Re-run data figure/table generation when source data changes. Re-run image-2/conceptual figure generation when the prompt, provenance, generation settings, sidecar/inspect/review evidence, or accepted output changes; do not regenerate an already accepted image merely to refresh metadata unless the original raw generation proof is missing.
   - Re-run benchmark or analysis work when page/body failures are caused by missing evidence: finish full-scale runs, add missing baselines, run ablations/sensitivity slices, produce failure taxonomy/error analysis, or generate public-validation/robustness tables before rewriting prose.
   - Compile the paper when LaTeX is available.
   - Invoke the EMNLP Format Preflight skill and run `validate-paper-format` plus `validate-research-md-format` after any LaTeX, figure, table, bibliography, or appendix-order edit; do not continue to assurance while it reports unresolved refs/citations, appendix-before-references ordering, placeholders, code-like display labels, missing figure/table constraints, or `Overfull \hbox > 5pt`.
   - After every prose-changing edit, run `python -m argus_skill.skills.academic_language_review --project-root . --review-mode model --write` and `python -m argus_skill.skills.pipeline_contracts validate-academic-language-review --project-root .`.
   - After every layout-affecting edit, run `python -m argus_skill.skills.paper_layout_review --project-root . --review-mode vision --write` and `python -m argus_skill.skills.pipeline_contracts validate-layout-review --project-root .`.

6. Iterate within the mission:
   - If compile fails, fix compile errors before declaring progress.
   - If academic-language review scores below 4/5, has blocking issues, fails a required check, or sets `needs_revision: true`, keep revising prose/claims/related work before layout-only work.
   - If layout review scores below 4/5, has blocking issues, or sets `needs_revision: true`, keep revising layout/content/figures/tables before any assurance PASS.
   - Stop after three non-improving academic-language rounds and mark the revision `blocked` with the remaining `paper/ACADEMIC_LANGUAGE_REVIEW.json` directives; do not self-assign a higher prose score.
   - Stop after three non-improving layout rounds and mark the revision `blocked` with the remaining `paper/LAYOUT_REVIEW.json` directives; do not self-assign a prettier score.
   - If the reviewer would still reject for an addressable issue, continue with concrete patches.
   - If more evidence is required, write a follow-up benchmark objective instead of inventing text.

7. Update `paper/PAPER_REVISION_LOG.md`:
   - Round summary, files edited, compile/test output, remaining blockers.
   - Include raw verification output for compile, academic-language review score, `validate-academic-language-review`, layout review score, `validate-layout-review`, and any analysis scripts.
   - Update `research/PIPELINE_STATE.json` with `revision` status and the next assurance action.

## Response shape
- Summarize the meaningful paper changes and remaining blockers.
- Include exact verification commands and outputs.
- If the draft is not submission-ready, say why plainly and name the next mission.
