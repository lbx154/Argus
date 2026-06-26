---
name: Benchmark Paper Figure Checklist
description: When drafting a benchmark / evaluation paper (introducing a new dataset, leaderboard, or evaluation protocol), enforce the canonical 5-6 figure types that benchmark papers must include — observed gap on v1 multimodal-bench shipped 0 figures despite mission requiring >=6. Use BEFORE entering draft stage, not after structural_minimums gate fires.
category: paper-figures
version: 1
created_at: 2026-06-09T00:00:00+00:00
---

# Benchmark Paper Figure Checklist — canonical 5-6 figure types

> Codifies a meta-knowledge gap observed on `agent-multimodal-reasoning-v1`:
> engineer rewrote 522-line MMR-Trap draft with **zero `\includegraphics` /
> zero `\begin{figure}` blocks**, despite the mission spec requiring ≥6
> figures and the `paper_structural_minimums` gate enforcing it at draft
> stage. Root cause: the gate only fires after PIPELINE_STATE flips to
> `draft/ready`, while overlap-mode drafting silently bypassed it. This
> skill moves the requirement upstream: at scaffold time, list the
> figure types so the engineer plans figs/ alongside section text.

## When to use

- Mission objective mentions: new benchmark, new dataset, leaderboard,
  evaluation protocol, evaluation suite, model comparison study.
- Engineer is about to write or has just written `paper/main.tex`.
- Stage is in `run`, `analysis`, or `draft`-preparation overlap.

Do NOT use when:
- The paper is a method paper (a new model / algorithm / training
  recipe); use `emnlp-paper-drafting` + `figure-spec` instead.
- The work is a position paper, survey, or theory contribution.

## What well-known benchmark papers actually contain

A high-impact benchmark paper (MMMU, MathVista, BIG-Bench, GLUE, SWE-Bench,
HellaSwag, HumanEval) almost always includes 5–6 figures from this canon:

| # | Type | Purpose | Common location |
|---|---|---|---|
| **F1** | **Teaser / motivating example** | Single image: one trap item + its control + the correct vs wrong answer, plus the headline finding ("X model scores Y%"). | Page 1, top of column 1 or full-width above intro. |
| **F2** | **Task / sample category distribution** | Pie chart, bar chart, or sunburst of items per category / split / difficulty. Justifies coverage. | Page 2-3, in Benchmark Construction. |
| **F3** | **Construction pipeline diagram** | Boxes-and-arrows of how items were generated, filtered, and verified. Often image-2 stylised. | Benchmark Construction section. |
| **F4** | **Qualitative example grid** | 4-8 example items across categories, with image + question + gold + sample wrong model output. | Benchmark Construction or Results. |
| **F5** | **Main results heatmap or grouped bar** | Model × category accuracy, often with error bars or confidence intervals. The leaderboard visual. | Results section, full-width preferred. |
| **F6** | **Error / failure analysis** | One of: confusion matrix, per-category gap (control - trap), severity/difficulty ladder, refusal-rate breakdown, error taxonomy bar. | Analysis section. |

Optional 7th: **cost / size vs quality scatter** (parameter count log-x,
accuracy y, one point per model) when the leaderboard spans wildly
different model sizes.

## Procedure

### 1. Before writing main.tex sections

For each figure F1–F6 above, write a 1-line spec into
`paper/FIGURE_PLAN.md`:

```markdown
# Figure plan (benchmark paper canon)

F1 teaser:               source=bench/dev_smoke/items.jsonl + summary.tsv (best vs worst)
F2 category distribution: source=bench/test/items.jsonl  category column
F3 construction pipeline: image-2 generation; prompt template in figs/F3_prompt.txt
F4 qualitative grid:     source=bench/dev_smoke/images/ + items.jsonl
F5 main results heatmap: source=summary_by_category_seed.tsv
F6 error / gap analysis: source=summary.tsv condition column (control - trap)
```

Commit this BEFORE adding any new sections to main.tex. Reviewer will
treat absence of FIGURE_PLAN.md as a structural blocker even if
`paper_structural_minimums` gate is not active yet.

### 2. Generate figures

- **Data figures (F2, F5, F6, optional cost-quality)**: write
  `code/make_figs.py` with matplotlib. ACL style: serif font (cm),
  fontsize 9, single-column width 3.34in / double-column 6.94in. Save
  as PDF vector under `paper/figs/`.
- **Conceptual figures (F1 teaser, F3 pipeline)**: follow
  `paper-illustration-image2.md` — gpt-image-2 generation with
  IMAGE2_FIGURES.json provenance and image-2 manifest. Do NOT redraw
  conceptual figures in matplotlib/TikZ; that violates the structural
  gate.
- **Qualitative grid (F4)**: stitch real benchmark item PNGs from
  `bench/dev_smoke/images/` via PIL into a single PDF. This is a data
  figure (real samples), not conceptual, so matplotlib/PIL is fine.

### 3. Insert into main.tex

Each figure gets `\begin{figure}[tb]\centering\includegraphics[width=
\linewidth]{figs/<id>.pdf}\caption{...}\label{fig:<id>}\end{figure}`
and a body reference `Figure~\ref{fig:<id>}`. Place F1 right after
`\maketitle` for teaser effect.

### 4. Page-budget check

ACL hard-caps body at 8 pages. Six figures + 9 base pages typically
expands to 10-11 pages. Demote in priority order: F6 → appendix, F4 →
appendix, then F2 if still over. F1, F3, F5 are non-negotiable in body
for a benchmark paper.

## Acceptance gate (use by reviewer)

The reviewer should fail-closed if:

- `paper/FIGURE_PLAN.md` is missing.
- `paper/main.tex` has 0 `\begin{figure}` blocks at draft-or-later
  stage.
- Fewer than 4 of the 6 canonical types are present in body.

`paper_structural_minimums` already catches some of this at draft
stage. This skill brings the check forward to scaffold time so the
engineer plans figs alongside prose.

## Why this exists

Observed on `agent-multimodal-reasoning-v1`: engineer wrote 9-page
MMR-Trap draft with 522 tex lines, 20 BibTeX entries, real eval data
in `summary.tsv` — and ZERO figures. The 6 figure-related skills in
the library (`figure-spec`, `paper-illustration-image2`,
`research-results-analysis-and-figures`, `paper-framework-figure-
studio-pro`, `figure_spec_scripts/`) all answer "how to make figures",
none answers "for benchmark papers, you MUST have these specific
figure types". Adding this checklist closes the gap.
