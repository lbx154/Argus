---
name: "EMNLP Format Preflight"
description: "Perform the final research.md formatting, PDF, figure/table, and layout-readiness preflight before academic-language and visual layout review."
---

## Title
EMNLP Format Preflight

## Description
Run the dedicated formatting check for an EMNLP/ACL paper draft before final paper review.

## When to use
- `paper/main.tex` and `paper/main.pdf` exist or the draft is about to be called complete.
- The draft has just changed figures, tables, page allocation, bibliography, appendices, title/author block, or LaTeX layout.
- The paper is moving from drafting/revision into final review.

## Required inputs
- `paper/main.tex`, all transitive `\input`, `\include`, `\subfile`, and BibTeX sources.
- `paper/main.pdf` and `paper/main.log` from the latest compile.
- `paper/PAPER_DRAFT_REPORT.json` with `target_venue: "EMNLP"`, `paper_scope: "long-paper"`, `official_acl_template: true`, `submission_phase: "review"` unless camera-ready, and `submission_quality_self_assessment: "ready"` only when all checks pass.
- `paper/PAGE_BUDGET.md`, `paper/TEMPLATE_SOURCE.md`, `paper/ARTIFACT_MANIFEST.json`, and current figure/table artifacts.

## Hard preflight contract
Treat every item below as blocking for a final EMNLP-ready claim:

1. **Template and anonymity**
   - Use the official ACL/EMNLP template, preferably `\usepackage[review]{acl}` from https://github.com/acl-org/acl-style-files.
   - In review mode the author block must be anonymous, e.g. `Anonymous EMNLP Submission`. Do not add real authors unless `submission_phase` is camera-ready/final.
   - Use the ACL/EMNLP author-year natbib style. Numeric citation overrides such as `\setcitestyle{numbers,square}`, `\usepackage[numbers]{natbib}`, or `\PassOptionsToPackage{numbers}{natbib}` are not acceptable for the review-paper package unless the operator explicitly changes the venue/style requirement.
   - Target 7.5--8 main-content pages excluding references and appendix; do not pad pilot evidence into a long-paper shell. The total article length is uncapped after the body: do not enforce any maximum page count for References or Appendix. The final PDF should visibly use the body budget: Conclusion should not appear before page 7 and must end by page 8, and References and any Appendix material must begin on page 9 or later. References or Appendix on page 8 usually mean the paper still has only about seven body pages. If the body is short, add or move source-backed body material before Conclusion: literature-grounded Introduction/Related Work framing, benchmark/Method detail, or evidence-bearing Results/Analysis/Ablation/Failure Cases material according to the page budget. If Conclusion moves after page 8 or `main_content_pages` exceeds 8.0, stop expanding and perform a page-map reflow: compress repeated body paragraphs, merge tiny sections, shorten captions/tables, and move appendix-like implementation/release material after References while preserving reader-facing abstract, Introduction, Method, and Experimental Setup substance. Text after Conclusion does not repair an underfilled main body. Do not put `\clearpage`, `\newpage`, `\pagebreak`, or `\FloatBarrier` immediately before Conclusion; that can leave page 8 mostly blank and push Conclusion to page 9 after small float changes.
   - When `conclusion_after_page_8` appears together with `severe_overfull_hbox`, treat it as a float/table design failure first. Keep one central cross-benchmark results matrix in the body, then move admission traces, implementation constants, verbose benchmark-provenance details, and secondary diagnostic tables to the appendix. Do not add more prose, add new body tables, or keep shrinking the same overfull table; rebuild the float plan so the main body reaches Conclusion on page 8 without sacrificing abstract/Introduction/Method/Setup substance.
   - If the body is short or visually thin, classify it as `content_sufficiency` rather than a cosmetic layout defect unless the evidence is already complete. Require one of: more benchmark runs, missing baseline/ablation completion, robustness/public-validation analysis, failure taxonomy/error analysis, source-backed Introduction/Related Work/Method expansion from verified literature/provenance, or claim downgrade. Do not accept larger fonts, looser spacing, repeated caveats, or oversized floats as a page-count fix.
   - Treat shallow core sections as content failures, not layout problems. Keep the abstract in the normal 170--220 word range, and let the academic-language reviewer judge Introduction/Method/Setup depth by paper function rather than exact word counts: cited problem/gap framing, method insight, quantified result preview, contribution roadmap, evaluated system details, benchmark harness, metrics, budget/decoding or scoring policy, seed policy, and stopping rules. If those functions are missing, route to drafting/evidence repair before layout tuning.

2. **Section order and completeness**
   - Conclusion must render by page 8 and should not appear before page 7 for a full long paper; if it lands on page 9 or later, reduce or move earlier body material instead of adding more Introduction/Method prose. It must arrive through normal body flow, not a forced manual page break immediately before the section.
   - Limitations and Ethical Considerations must appear after the conclusion.
   - References must appear before any Appendix.
   - References and any Appendix material must start on page 9 or later; after that boundary, the total number of reference/appendix pages is unlimited.
   - Include a complete reproducibility appendix with a neutral replay interface: paper-facing regeneration targets or command aliases, seed policy, evaluated model/backend IDs, public data/source versions, artifact-type inventory, and verification notes. Do not render local runner script names, raw run IDs, private experiment directory paths, cache/device settings, or project-local artifact paths as the paper-facing reproducibility interface; keep those details in manifests, logs, or supplementary package metadata.
   - Method/Experimental Setup must describe the actual research object,
     public evidence, strongest relevant comparisons, metrics, uncertainty method,
     and relevant configuration without assuming an agent/controller design.
   - Setup/configuration and result tables must look like paper tables, not internal logs. They should include explicit `Benchmark`/`Source` and `Model`/`Backend` columns, task count/split, method or baseline role, metric, budget/decoding, and key result; they must not show `engineer`/`reviewer` route labels, `gpt-5.5*`, Argus/Codex configuration, validator names, or capability-vault details.
   - Final results need a readable, domain-appropriate evidence presentation
     covering the selected public source(s), strongest relevant comparisons, and
     claim-critical outcomes. Do not force a cross-benchmark matrix or fixed
     source count.

3. **Compile/PDF cleanliness**
   - No undefined references or citation warnings in `paper/main.log`.
   - No rendered `[?]` markers in `paper/main.pdf` text.
   - No `Overfull \hbox > 5pt`.
   - No placeholders, TODO/TBD/FIXME, `\textbf{[PLACEHOLDER]}`, `[VERIFY_CITATION]`, or `% UNVERIFIED` bibliography entries.
   - Final-ready drafts must have bibliography depth: at least 35 verified BibTeX entries, at least 30 unique cited keys in the paper source, and, when PDF text extraction is available, References must occupy at least two rendered pages before the Appendix.
   - Reference formatting must look like a real ACL bibliography: no rendered `and 1 others`/`and N others`, no title-only labels from missing author/editor/organization metadata, no BibTeX `author={... and others}` or `et al.` placeholders, no citation commands that dump more than eight keys, and no dense related-work paragraph that functions as a bibliography pile. Fetch verified BibTeX from ACL Anthology, Semantic Scholar, arXiv, CrossRef, DBLP, or official proceedings pages and preserve capitalization with braces where needed.
   - Verify BibTeX semantically, not only syntactically: citation key, title, authors, year, DOI/arXiv/ACL URL, and venue must refer to the same paper. Starter targets are search targets, not safe BibTeX. A key like `amem2025`, `longmem2024`, `webrl2024`, or `hallucinationsurvey2023` paired with an unrelated title is a hard citation failure; refetch the entry instead of renaming it.
   - References must start cleanly after the body. Do not let the References heading share a rendered page or column with Conclusion, Limitations, or Ethical Considerations. If the body is already full, insert a clean reference break after body end matter; if Conclusion is early, expand or move source-backed body content before Conclusion instead of shortening the paper. Never move the reference-boundary break before Conclusion.
   - No code-font/snake_case display labels in title, abstract, headings, captions, figures, or tables unless the exception is explicitly listed in `allowed_code_labels`.

4. **Figures and layout**
   - Every body figure has a `\label{}` and is referenced in the text with `\ref`, `\autoref`, `\cref`, or equivalent.
   - Body figures are capped at five total; at most one may be a full-width `figure*`.
   - The middle body should have meaningful visual anchors when they improve readability; rely on the model-backed layout review for page-rhythm judgment instead of inserting low-value floats to hit fixed page numbers.
   - Judge the actual rendered figures for readability, factual correctness, and good-enough visual quality. `FIGURE_PROVENANCE.json` is optional handoff metadata, not a format blocker. Do not request repeated regeneration for minor stylistic preferences.

5. **Tables**
   - Every table caption must state a numerical headline, not just describe contents.
   - Comparative binary or paired outcomes need at least one paired-significance table; otherwise set `paired_significance_not_applicable: true` with a rationale in `paper/PAPER_DRAFT_REPORT.json`.
   - Tables must use the `research.md` style tokens: `\footnotesize`, `\tabcolsep=3-4pt`, `\arraystretch=1.15`, light-gray header, soft peach "ours" row, alternating row tint for long tables, coral accent only for meaningful degradation, and bold winning values.
   - If the same table triggers `severe_overfull_hbox` twice, stop micro-adjusting `\tabcolsep`, column widths, or `\shortstack` headers. Change the representation instead: split the matrix into per-benchmark narrow tables, rotate the comparison into a vertical `Benchmark` x `Method` table, or move secondary columns to appendix while preserving the body headline. For generated Python LaTeX strings, prefer helper functions or doubled braces in raw f-strings before adding `\shortstack`; a syntax-error loop is a formatting failure, not progress.
   - For a draft that already has 3+ executed benchmark families, the body does not need separate full-width tables for setup, transfer, family breakdown, admission trace, and diagnostics. Prefer one professional main matrix plus one compact analysis table; appendix the rest and reference it once.

## Procedure
1. Compile from the project root and keep the latest log:
   - `latexmk -pdf -interaction=nonstopmode -halt-on-error -output-directory=paper paper/main.tex`
   - If `latexmk` is unavailable, run `pdflatex -output-directory=paper`/`bibtex paper/main`/`pdflatex -output-directory=paper`/`pdflatex -output-directory=paper` and save `paper/main.log`.
   - Do not rely on root-level `main.pdf` or `main.log`; validators read `paper/main.pdf` and `paper/main.log`, and a newer root-level build is a format failure.
2. Inspect the source and PDF:
   - Self-audit the `research.md` format-preflight requirements before claiming readiness; the L2 reviewer verifies these artifacts directly against the draft/submission stage checklists.
   - If the command reports any issue, fix the LaTeX/source/artifact and rerun; do not continue to layout review.
3. Write `paper/FORMAT_PREFLIGHT.md` with:
   - compile command and status;
   - page count and conclusion page;
   - figure/table inventory with labels, refs, captions, and page placement;
   - bibliography verification status, verified entry count, unique cited-key count, and rendered reference-page count;
   - every fix made during preflight;
   - the exact final format-preflight self-audit result.
4. Only after this command is clean, run:
   - `python -m argus_skill.verticals.research.academic_language_review --project-root . --review-mode model --write`
   - `python -m argus_skill.verticals.research.paper_layout_review --project-root . --review-mode vision --write`
   - self-audit the academic-language review thresholds
   - self-audit the layout review thresholds

## Response shape
- State whether the `research.md` format-preflight requirements hold.
- If it failed, list the blocking issue codes and changed files.
- If it passed, name `paper/FORMAT_PREFLIGHT.md`, `paper/main.pdf`, and the next required review artifact.
