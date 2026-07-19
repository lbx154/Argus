---
name: AAAI Format Preflight
description: Final AAAI-2026 formatting, PDF, figure/table, and layout-readiness preflight (aaai2026.sty page budget, \pdfinfo, forbidden packages, no \bibliographystyle) before academic-language and visual layout review.
category: paper-audit
version: 1
created_at: 2026-06-14T00:00:00+00:00
---

## Title
AAAI Format Preflight

## Description
Run the dedicated formatting gate for an AAAI-2026 paper draft. This skill owns the strict `research.md` submission-format contract before the model-backed academic-language review, vision layout review, and submission assurance gate are allowed to pass. The contract is anchored to the official `aaai2026.sty`/`aaai2026.bst` Author Kit: the `\pdfinfo` template stamp, the 7-page technical-content budget, the forbidden-package/command set, and the rule that the style file (not the author) sets the bibliography style.

## When to use
- `paper/main.tex` and `paper/main.pdf` exist or the draft is about to be called complete.
- The draft has just changed figures, tables, page allocation, bibliography, the reproducibility checklist, title/author block, or LaTeX layout.
- The pipeline is moving from drafting/revision into final review or submission assurance.

## Required inputs
- Single-file `paper/main.tex` (AAAI requires one `.tex` source — no `\input`/`\include`/`\subfile`) and the BibTeX source `paper/aaai2026.bib`.
- `paper/main.pdf` and `paper/main.log` from the latest `pdflatex` compile.
- `paper/PAPER_DRAFT_REPORT.json` with `target_venue: "AAAI"`, `official_aaai_template: true`, `submission_phase: "review"` unless camera-ready, and `submission_quality_self_assessment: "ready"` only when all checks pass.
- `paper/PAGE_BUDGET.md`, `paper/TEMPLATE_SOURCE.md`, `paper/ARTIFACT_MANIFEST.json`, and current figure/table artifacts.

## Hard preflight contract
Treat every item below as blocking for a final AAAI-ready claim:

1. **Template, preamble, and anonymity**
   - Use the official AAAI Author Kit (`aaai2026.sty` + `aaai2026.bst`) and record where it was obtained — source URL, retrieval date, and the `aaai2026.sty` sha256 — in `paper/TEMPLATE_SOURCE.md`. Acquire in this order: (1) an operator-provided/local kit path; (2) the official AAAI-26 Author Kit / Overleaf template (`https://aaai.org/conference/aaai/aaai-26/` → Author Kit) — authoritative for submission; (3) only if neither is reachable, the community mirror `git clone --depth 1 https://github.com/lizhemin15/AAAI-2026-Latex-Unified` for local compilation, recorded as `source: mirror (unverified)`. Do NOT clone `acl-org/acl-style-files`; that is the wrong venue's kit. **Blocking for submission readiness:** if `paper/TEMPLATE_SOURCE.md` marks the style as `mirror (unverified)`, the paper is NOT camera-ready — the `aaai2026.sty` must come from (or be diffed byte-for-byte against) the official kit, since a modified style sheet causes desk rejection.
   - The preamble must be copied from the kit, with the version string copied verbatim (never hardcoded from memory). The structural stamp is blocking: `\usepackage{aaai2026}` must be present (anonymous review uses `\usepackage[submission]{aaai2026}`; camera-ready drops the `[submission]` option), `\pdfinfo{ /TemplateVersion (2026.1) }` must be present, and `\setcounter{secnumdepth}{0}` must be present. Canonical preamble shape:
     ```
     \documentclass[letterpaper]{article}
     \usepackage[submission]{aaai2026}   % camera-ready uses \usepackage{aaai2026}
     \usepackage{times}\usepackage{helvet}\usepackage{courier}
     \usepackage[hyphens]{url}\usepackage{graphicx}\usepackage{natbib}\usepackage{caption}
     \pdfinfo{ /TemplateVersion (2026.1) }
     \setcounter{secnumdepth}{0}
     ```
   - Two-column, US-letter, 10pt, Times font. Computer Modern is forbidden for body text and no Type-3 fonts may appear in the PDF. Figures must be `.png`/`.jpg`/`.pdf` only (no `.eps`/`.ps`). Compile with `pdflatex` from a single `.tex` source.
   - **Forbidden packages** (incompatible with `aaai2026.sty` or explicitly disallowed): `hyperref`, `navigator`, `authblk`, `geometry`, `fullpage`, `titlesec`, `lmodern`, and similar layout/geometry overrides. **Forbidden commands**: `\pagestyle` (no page numbers), `\nocopyright`, `\vspace`, `\newpage`, `\clearpage`, `\pagebreak`, `\input`, `\renewcommand`, `\setlength`. Any of these present is a blocking failure.
   - In anonymous review the `[submission]` option renders the author block as the literal `Anonymous submission` and disables `\thanks` in the title block. Do not add real author names or affiliations unless `submission_phase` is camera-ready/final.
   - `\nocopyright` is forbidden: for an accepted paper it suppresses the mandatory copyright notice and "your paper will not be published." The copyright notice lives in the sty and may not be disabled. At the anonymous-submission stage the `[submission]` option already shows no copyright footer — do not try to add or remove the notice yourself.
   - Use AAAI author-year natbib citations rendered by `aaai2026.bst`. Numeric citation overrides such as `\setcitestyle{numbers,square}`, `\usepackage[numbers]{natbib}`, or `\PassOptionsToPackage{numbers}{natbib}` are not acceptable for `aaai2026.sty` unless the operator explicitly changes the venue/style requirement.
   - Target the full 7 pages of TECHNICAL CONTENT excluding References and the Reproducibility Checklist; do not pad pilot evidence into a full-paper shell. References and the Reproducibility Checklist sit on ADDITIONAL pages that do NOT count toward the 7 and are uncapped: do not enforce any maximum page count for that end matter. The final PDF should visibly use the body budget: Conclusion should not appear before page 6 and must end by page 7, and References (followed by the Reproducibility Checklist and any optional Appendix) must begin on page 8 or later. References landing on page 7 usually means the paper still has only about six body pages. If the body is short, add or move source-backed body material before Conclusion: literature-grounded Introduction/Related Work framing, benchmark/Method detail, or evidence-bearing Results/Analysis/Ablation/Failure Cases material according to the page budget. If Conclusion moves after page 7 or `main_content_pages` exceeds 7.0, stop expanding and perform a page-map reflow: compress repeated body paragraphs, merge tiny sections, and shorten captions/tables while preserving reader-facing abstract, Introduction, Method, and Experimental Setup substance. Text after References does not repair an underfilled main body. Because `\clearpage`, `\newpage`, `\pagebreak`, and `\vspace` are forbidden, you cannot force the boundary with a manual break; Conclusion must reach page 7 through real body content.
   - When `conclusion_after_page_7` appears together with `severe_overfull_hbox`, treat it as a float/table design failure first. Keep one central cross-benchmark results matrix in the body, then move admission traces, implementation constants, verbose benchmark-provenance details, and secondary diagnostic tables to the appendix after the Reproducibility Checklist. Do not add more prose, add new body tables, or keep shrinking the same overfull table; rebuild the float plan so the main body reaches Conclusion on page 7 without sacrificing abstract/Introduction/Method/Setup substance.
   - If the body is short or visually thin, classify it as `content_sufficiency` rather than a cosmetic layout defect unless the evidence is already complete. Require one of: more benchmark runs, missing baseline/ablation completion, robustness/public-validation analysis, failure taxonomy/error analysis, source-backed Introduction/Related Work/Method expansion from verified literature/provenance, or claim downgrade. Do not accept larger fonts, looser spacing, repeated caveats, or oversized floats as a page-count fix (and note that the spacing/geometry commands needed for those hacks are forbidden anyway).
   - Treat shallow core sections as content failures, not layout problems. AAAI sets NO official abstract word limit, so do not enforce a hard word count — treat any word target as advisory only and let the academic-language reviewer judge Introduction/Method/Setup depth by paper function rather than exact word counts: cited problem/gap framing, method insight, quantified result preview, contribution roadmap, evaluated system details, benchmark harness, metrics, budget/decoding or scoring policy, seed policy, and stopping rules. If those functions are missing, route to drafting/evidence repair before layout tuning.

2. **Section order and completeness**
   - Conclusion must render by page 7 and should not appear before page 6 for a full paper; if it lands on page 8 or later, reduce or move earlier body material instead of adding more Introduction/Method prose. It must arrive through normal body flow, not a forced manual page break (which is forbidden anyway).
   - AAAI does NOT require "Limitations" or "Ethical Considerations" sections — those are ACL/ARR conventions, not AAAI. Do not add them as mandatory sections.
   - A Reproducibility Checklist is REQUIRED and must be placed in the PDF AFTER the References (not before). Typical order: Abstract, Introduction, Related Work, Method, Experiments/Setup, Results, Analysis/Ablation, Conclusion, optional Acknowledgments (unnumbered, omitted in anonymous submission), References, Reproducibility Checklist, optional Appendix.
   - References must begin on page 8 or later; after that boundary the total number of reference/checklist/appendix pages is unlimited.
   - Complete the Reproducibility Checklist with a neutral replay interface: paper-facing regeneration targets or command aliases, seed policy, evaluated model/backend IDs, public data/source versions, artifact-type inventory, and verification notes. Do not render local runner script names, raw run IDs, private experiment directory paths, cache/device settings, or project-local artifact paths as the paper-facing reproducibility interface; keep those details in manifests, logs, or supplementary package metadata.
   - Method/Experimental Setup must describe the actual research object,
     public evidence, strongest relevant comparisons, metrics, uncertainty method,
     and relevant configuration without assuming an agent/controller design.
   - Setup/configuration and result tables must look like paper tables, not internal logs. They should include explicit `Benchmark`/`Source` and `Model`/`Backend` columns, task count/split, method or baseline role, metric, budget/decoding, and key result; they must not show `engineer`/`reviewer` route labels, `gpt-5.5*`, Argus/Codex configuration, validator names, or capability-vault details.
   - Final results need a readable, domain-appropriate evidence presentation
     covering the selected public source(s), strongest relevant comparisons, and
     claim-critical outcomes. Do not force a cross-benchmark matrix or fixed
     source count.

3. **Compile/PDF cleanliness and bibliography**
   - No undefined references or citation warnings in `paper/main.log`.
   - No rendered `[?]` markers in `paper/main.pdf` text.
   - No `Overfull \hbox > 5pt`.
   - No placeholders, TODO/TBD/FIXME, `\textbf{[PLACEHOLDER]}`, `[VERIFY_CITATION]`, or `% UNVERIFIED` bibliography entries.
   - **The style file sets the bibliography style — do NOT emit `\bibliographystyle{...}`.** `aaai2026.sty` applies `aaai2026.bst` automatically; an explicit `\bibliographystyle` raises `Illegal, another \bibstyle command` and is a blocking failure. End the paper with `\bibliography{aaai2026}` (your `.bib` named `aaai2026.bib`) and let the sty pick the style.
   - Final-ready drafts must have bibliography depth: at least 35 verified BibTeX entries, at least 30 unique cited keys in the paper source, and, when PDF text extraction is available, References must occupy at least two rendered pages before the Reproducibility Checklist.
   - Reference formatting must look like a real AAAI bibliography: no rendered `and 1 others`/`and N others`, no title-only labels from missing author/editor/organization metadata, no BibTeX `author={... and others}` or `et al.` placeholders, no citation commands that dump more than eight keys, and no dense related-work paragraph that functions as a bibliography pile. Fetch verified BibTeX from Semantic Scholar, arXiv, DBLP, CrossRef, ACL Anthology, or official proceedings pages and preserve capitalization with braces where needed.
   - Verify BibTeX semantically, not only syntactically: citation key, title, authors, year, DOI/arXiv URL, and venue must refer to the same paper. Starter targets are search targets, not safe BibTeX. A key like `amem2025`, `longmem2024`, `webrl2024`, or `hallucinationsurvey2023` paired with an unrelated title is a hard citation failure; refetch the entry instead of renaming it.
   - References must start cleanly after the body. Do not let the References heading share a rendered page or column with Conclusion. If the body is already full, the boundary falls naturally; if Conclusion is early, expand or move source-backed body content before Conclusion instead of shortening the paper. Never try to pull the reference boundary before Conclusion (and remember manual page breaks are forbidden).
   - No code-font/snake_case display labels in title, abstract, headings, captions, figures, or tables unless the exception is explicitly listed in `allowed_code_labels`.

4. **Figures and layout**
   - Every body figure has a `\label{}` and is referenced in the text with `\ref`, `\autoref`, `\cref`, or equivalent.
   - Body figures are capped at five total; at most one may be a full-width `figure*`.
   - Figure files must be `.png`/`.jpg`/`.pdf` (no `.eps`/`.ps`).
   - The middle body should have meaningful visual anchors when they improve readability; rely on the model-backed layout review for page-rhythm judgment instead of inserting low-value floats to hit fixed page numbers.
   - Judge the actual rendered figures for readability, factual correctness, and good-enough visual quality. `FIGURE_PROVENANCE.json` is optional handoff metadata, not a format blocker. Do not request repeated regeneration for minor stylistic preferences.

5. **Tables**
   - Every table caption must state a numerical headline, not just describe contents.
   - Comparative binary or paired outcomes need at least one paired-significance table; otherwise set `paired_significance_not_applicable: true` with a rationale in `paper/PAPER_DRAFT_REPORT.json`.
   - Tables should use the `research.md` visual style: `\footnotesize`, compact columns, a light-gray header, a soft peach "ours" row, alternating row tint for long tables, a coral accent only for meaningful degradation, and bold winning values. AAAI forbids `\setlength`, `\renewcommand`, and `\vspace`, so do not tune `\tabcolsep`/`\arraystretch` or row spacing with document-level `\setlength`/`\renewcommand`; if you need tighter columns, consult the official Author Kit for the sanctioned mechanism and mark it as a TODO rather than guessing.
   - If the same table triggers `severe_overfull_hbox` twice, stop micro-adjusting column widths or `\shortstack` headers. Change the representation instead: split the matrix into per-benchmark narrow tables, rotate the comparison into a vertical `Benchmark` x `Method` table, or move secondary columns to the appendix while preserving the body headline. For generated Python LaTeX strings, prefer helper functions or doubled braces in raw f-strings before adding `\shortstack`; a syntax-error loop is a formatting failure, not progress.
   - For a draft that already has 3+ executed benchmark families, the body does not need separate full-width tables for setup, transfer, family breakdown, admission trace, and diagnostics. Prefer one professional main matrix plus one compact analysis table; appendix the rest (after the Reproducibility Checklist) and reference it once.

## Procedure
1. Compile from the project root with `pdflatex` and keep the latest log:
   - `latexmk -pdf -interaction=nonstopmode -halt-on-error -output-directory=paper paper/main.tex`
   - If `latexmk` is unavailable, run `pdflatex -output-directory=paper`/`bibtex paper/main`/`pdflatex -output-directory=paper`/`pdflatex -output-directory=paper` and save `paper/main.log`.
   - Do not rely on root-level `main.pdf` or `main.log`; validators read `paper/main.pdf` and `paper/main.log`, and a newer root-level build is a format failure.
2. Inspect the source and PDF:
   - Self-audit the `research.md` format-preflight requirements before claiming readiness; the L2 reviewer verifies these artifacts directly against the draft/submission stage checklists.
   - If the command reports any issue, fix the LaTeX/source/artifact and rerun; do not continue to layout review.
3. Write `paper/FORMAT_PREFLIGHT.md` with:
   - compile command and status;
   - page count and conclusion page;
   - confirmation of the AAAI structural gate: `\pdfinfo` present, `\usepackage{aaai2026}` present, no `\bibliographystyle`, no `hyperref`/`navigator`, no `\nocopyright`, no `\pagestyle`/page numbers, Conclusion by page 7, References page 8+, Reproducibility Checklist after References;
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
