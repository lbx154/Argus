---
name: "Paper Chart Styling"
description: "Give every DATA figure in a paper one consistent, journal-grade look instead of default-matplotlib ugliness. Use when generating accuracy/latency/ablation plots, bar/line charts, or any data-driven figure for a paper. Covers a shared publication style (SciencePlots + colour-blind-safe palettes), venue-aware figure sizing (single-column figure vs full-width figure*), redundant colour+marker encoding, highlighting the proposed method, correct PDF font embedding, and learning composition from open-access exemplar papers. Applies to any venue — column layout comes from the project's researched venue profile."
---

## Title
Paper Chart Styling

## Description
Data figures generated ad-hoc look nothing like a real conference paper: default
blue/orange, rainbow/`jet` colormaps, wrong font sizes, no font embedding, and
colours that collapse under colour-blind simulation. This skill gives every data
plot ONE shared, journal-grade style via a small helper, `paper_chart_style.py`,
and a short set of composition rules learned from open-access papers. Conceptual
figures (teaser/pipeline/architecture) are NOT covered here — route those through
the research vertical's Research Visualization Router. This skill is only for
**data/metric/result plots that are legitimately scripted from local data**.

## When to use
- You are creating data-driven figures (curves, bars, scatter, heatmaps) for a
  paper from `paper/analysis/build_results.py` or similar.
- The figures currently look inconsistent, off-palette, or "ugly" versus a real
  conference paper.

## When NOT to use
- Conceptual/method/teaser/pipeline overview figures — use the Research
  Visualization Router rather than disguising them as data plots.
- There is no local data to plot yet (run/analyze experiments first).

## How to solve

1. **Install the required plotting stack in the project venv**:
   ```bash
   pip install 'argus-skill[figures]'
   # or: pip install matplotlib seaborn SciencePlots
   ```
   Do not continue with plain matplotlib or a hand-authored SVG data plot when
   this dependency is missing. Surface the installation failure and repair the
   project environment.

2. **Copy the shared style helper into the project** so analysis scripts can
   import it without `argus_skill` on the path:
   ```bash
   python - <<'PY'
   import shutil
   from pathlib import Path
   from argus_skill.verticals.research.skills.engineer.figure_spec_scripts import paper_chart_style
   src = Path(paper_chart_style.__file__)
   Path("paper/analysis").mkdir(parents=True, exist_ok=True)
   shutil.copy(src, "paper/analysis/paper_chart_style.py")
   print("copied ->", "paper/analysis/paper_chart_style.py")
   PY
   ```

3. **Apply the style once at the top of the analysis script**, before creating
   any figure. The style reads the column layout (one- vs two-column template)
   from the project's researched `research/VENUE_PROFILE.json`, so sizes match
   the venue template automatically:
   ```python
   from paper_chart_style import set_pub_style, figure_size, highlight_ours
   import matplotlib.pyplot as plt

   colors = set_pub_style(column="double", palette="colorblind")
   # If the script runs outside the project tree, pass the layout explicitly:
   # colors = set_pub_style(column="double", two_column=True, palette="colorblind")
   ```
   - `palette` is one of `colorblind` (default), `muted` (cool journal tone), or
     `high_contrast` (talks/posters). All three are colour-blind-safe.

4. **Size each figure for the LaTeX float it will sit in** — this is what keeps
   fonts crisp (LaTeX rescaling a wrongly-sized graphic is what warps text):
   - Full-width `figure*` (teaser, main results panel): `figure_size("double")`.
   - Single-column `figure` (ablation, per-component): `figure_size("single")`.
   ```python
   fig, ax = plt.subplots(figsize=figure_size("single"))
   ```

5. **Encode redundantly and highlight the proposed method** so the figure reads
   in greyscale and under CVD, and the reader's eye lands on "Ours":
   - Vary `marker` and `linestyle` per series in addition to colour (e.g.
     markers `o`/`s`/`D`/`^`, linestyles `-`/`--`/`:`).
   - Call `highlight_ours(ax, ours_index=<i>)` to fade baselines to neutral grey
     and give the proposed series full saturation + a dark outline / heavier weight.
   - Axes carry units (`accuracy (%)`, `latency (ms)`); prefer direct labels or a
     small legend over a giant legend box.

6. **Never use rainbow/`jet`.** For sequential data use `viridis`/`cividis`; for
   diverging data use `coolwarm`. Keep grids subtle, spines thin.

7. **Choose chart grammar from the estimand, not visual novelty.** Do not use
   3-D charts, dual axes, or truncated axes without a claim-relevant,
   explicitly disclosed reason. Show uncertainty when the claim depends on it,
   keep comparable panels on consistent scales, and prevent legends,
   annotations, and labels from covering data. Omit an in-plot title when the
   caption already identifies the figure.

8. **Save vector/high-dpi with embedded fonts** (the helper sets `pdf.fonttype=42`
   and 600 dpi): generate PDF, SVG, and a high-DPI PNG review artifact from the
   same plotting script. Embed the PDF in the paper; do not replace the data
   renderer with manually constructed SVG primitives.

9. **Learn composition from real papers.** Before locking figure layouts, run the
   **Paper Exemplar PDF Learning** skill and study how 2–3 open-access papers in
   the same area compose their data figures: how many panels, axis conventions,
   how they highlight their own method, legend placement, and caption phrasing.
   Match those conventions; do not copy their data or exact styling verbatim.

10. **Drive figure choice from the current draft.** Decide which figures the
    story needs (main result curve, key ablation, cost/quality trade-off) rather
    than dumping every metric. Each figure should support a specific claim.

11. **Inspect the exported PDF at final use size.** Check clipping, crowded or
    ambiguous ticks, legend/data overlap, panel alignment, font embedding,
    grayscale discrimination, and whether labels remain readable at the actual
    single- or double-column width.

## Notes
- The helper is dependency-light and self-contained; the copy in `paper/analysis/`
  is what your scripts import. Re-copy it if you upgrade.
- SciencePlots is mandatory for this route. A missing package is an environment
  error, not permission to fall back to the retired ad-hoc data-figure method.
- This skill styles data plots only. Conceptual/method figures use the
  renderer-neutral Research Visualization Router.
- Figure width must still agree with the LaTeX float type: teaser and the main
  pipeline/architecture overview are the full-width `figure*` floats; sub-module
  and detail plots stay single-column `figure` (the layout review flags an
  overview/teaser/pipeline graphic left in a single column).
