---
name: "Paper Chart Styling"
description: "Give every DATA figure in a paper one consistent, journal-grade look instead of default-matplotlib ugliness. Use when generating accuracy/latency/ablation plots, bar/line charts, or any data-driven figure for a paper. Covers a shared publication style (SciencePlots + colour-blind-safe palettes), venue-aware figure sizing (single-column figure vs full-width figure*), redundant colour+marker encoding, highlighting the proposed method, correct PDF font embedding, and learning composition from open-access exemplar papers. Applies to any venue (EMNLP/AAAI/NeurIPS/…)."
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

1. **Install the styling packages in the project venv** (fail-soft — if they
   cannot be installed, the helper falls back to plain matplotlib and you keep
   going):
   ```bash
   pip install matplotlib seaborn SciencePlots
   ```

2. **Copy the shared style helper into the project** so analysis scripts can
   import it without `argus_skill` on the path:
   ```bash
   python - <<'PY'
   import argus_skill, os, shutil
   src = os.path.join(os.path.dirname(argus_skill.__file__),
                      "builtin_skills", "engineer", "figure_spec_scripts",
                      "paper_chart_style.py")
   os.makedirs("paper/analysis", exist_ok=True)
   shutil.copy(src, "paper/analysis/paper_chart_style.py")
   print("copied ->", "paper/analysis/paper_chart_style.py")
   PY
   ```

3. **Apply the style once at the top of the analysis script**, before creating
   any figure. Resolve the venue from the active profile so sizes match the
   template:
   ```python
   from paper_chart_style import set_pub_style, figure_size, highlight_ours
   import matplotlib.pyplot as plt

   # venue: "EMNLP" | "AAAI" | the researched key (e.g. "NEURIPS")
   colors = set_pub_style(venue="EMNLP", column="double", palette="colorblind")
   ```
   - `palette` is one of `colorblind` (default), `muted` (cool journal tone), or
     `high_contrast` (talks/posters). All three are colour-blind-safe.

4. **Size each figure for the LaTeX float it will sit in** — this is what keeps
   fonts crisp (LaTeX rescaling a wrongly-sized graphic is what warps text):
   - Full-width `figure*` (teaser, main results panel): `figure_size("double", venue=...)`.
   - Single-column `figure` (ablation, per-component): `figure_size("single", venue=...)`.
   ```python
   fig, ax = plt.subplots(figsize=figure_size("single", venue="EMNLP"))
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

7. **Save vector/high-dpi with embedded fonts** (the helper sets `pdf.fonttype=42`
   and 600 dpi): prefer `fig.savefig("paper/figures/<name>.pdf")`; a `.png` is
   acceptable as a fallback only.

8. **Learn composition from real papers.** Before locking figure layouts, run the
   **Paper Exemplar PDF Learning** skill and study how 2–3 open-access papers in
   the same area compose their data figures: how many panels, axis conventions,
   how they highlight their own method, legend placement, and caption phrasing.
   Match those conventions; do not copy their data or exact styling verbatim.

9. **Drive figure choice from the Draft.** Use `paper/PAPER_STRUCTURE_BLUEPRINT.md`
   / the current draft to decide WHICH figures the story needs (main result curve,
   key ablation, cost/quality trade-off) rather than dumping every metric. Each
   figure should support a specific claim in the draft.

## Notes
- The helper is dependency-light and self-contained; the copy in `paper/analysis/`
  is what your scripts import. Re-copy it if you upgrade.
- This skill styles data plots only. Conceptual/method figures use the
  renderer-neutral Research Visualization Router and `FIGURE_PROVENANCE.json`.
- Figure width must still agree with the LaTeX float type: teaser and the main
  pipeline/architecture overview are the full-width `figure*` floats; sub-module
  and detail plots stay single-column `figure` (the layout review flags an
  overview/teaser/pipeline graphic left in a single column).
