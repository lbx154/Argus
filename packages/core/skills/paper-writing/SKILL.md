---
name: paper-writing
description: "Write, illustrate, polish and audit camera-ready conference and journal papers: narrative arc, page-1 hero teaser, varied publication figures, appendix structure, and deterministic PDF/layout checks"
---

# Writing and Illustrating a Camera-Ready Paper

A paper is an argument built for a human referee, not an experiment log stitched together at the end. Every number in it comes from a real run or a verified primary source, generated into LaTeX macros (`paper/gen/*.tex`) so prose, tables and figures cannot drift apart.

## 1. How to structure the work

1. **Manuscript skeleton early, wired by macros.** Pick the target venue and its official LaTeX template first, record its main-body page limit in `paper/PLAN.md`, define `\newcommand{\ours}{\textsc{...}}`, and draft the narrative arc, section structure, formal setup and table/figure contracts while experiments run—never wait until the final hour to dump raw JSON logs into prose.
2. **Plan the figure suite before drawing (`paper/figs/FIGURE_PLAN.md`).** List every claim, which table or dataset backs it, and assign each figure a **distinct visual encoding** so no two figures repeat the same layout:
   - **CS / ML conference papers:** 1 page-1 hero teaser (`Figure 1`), 1 method/mechanism diagram (`Figure 2`), 2–4 varied quantitative figures (e.g., Pareto accuracy-vs-cost frontier, paired interval/forest plot, phase heatmap or distribution violin+strip plot), and at least 1 concrete qualitative/case-study comparison on real inputs and outputs.
   - **Economics / finance / management journals:** 2–4 restrained journal-style figures via `econ-figure` (event-study dynamic effects, heterogeneity `coefplot`, `binscatter`, placebo distribution); no CS-style teaser or decorative boxes.
   - **Pure theory / math papers:** 1–3 high-clarity figures (counterexample construction, lemma dependency DAG, and theory-curve vs. empirical-points verification); consult `theory-tricks`.
3. **Editorial polish + deterministic audit before Reviewer hand-off.** Once all real results land, run one whole-paper editorial pass to unify terminology, rewrite robotic number-dump paragraphs into analytical prose, balance page fill, and pass both scripts in this skill folder with zero blocking findings:
   ```bash
   python3 <this-skill-dir>/audit_paper.py paper/main.pdf --tex-dir paper --max-body-pages 8
   python3 <this-skill-dir>/tile_figures.py paper/figs/*.pdf --out /tmp/fig-tiles
   ```

## 2. Narrative and prose craft

- **Title:** Write a crisp, memorable title—usually `MethodName: <Concrete Insight or Mechanism Phrase>`—never generic openers (`Towards`, `A Novel`, `On the`, `Efficient`) or a laundry list of settings. When breaking a title across lines with `\\`, wrap it in `\texorpdfstring{Line 1 \\ Line 2}{Line 1 Line 2}` so PDF metadata never glues words together.
- **Page-1 Hero Teaser (`Figure 1`):** For CS/ML conference papers, anchor `Figure 1` on **Page 1** (above the abstract via `\twocolumn[{...}]` where the template permits, or `[t!]` at the top of Page 1–2; never let it drift to Page 3+). Structure it in 2–3 complementary panels `(a)(b)(c)`:
  - `(a)` Why prior paradigms fail (compact factor contrast or concrete failure case);
  - `(b)` How `\ours` works on **one real concrete example** (actual tokens/sentence, real dataset crop, real trajectory or score trace—**plain text boxes must occupy under 50% of the canvas**);
  - `(c)` Headline measured result or accuracy-cost trade-off from real runs.
- **Introduction (four moves):**
  1. Ground the problem in 2–3 concrete, cited scenarios showing where existing methods fail and what that failure costs;
  2. State the core question in *one italicized sentence*;
  3. Explain the underlying tension—why naive combinations or existing heuristics fail;
  4. Introduce `\ours`, summarize the headline measured findings (including honest boundary regimes), and end with a **Contribution list where every bullet closes with exact evidence coordinates**, e.g. `(Section 3, Theorem 1)`, `(Section 4, Algorithm 1)`, `(Section 6, Figures 3–5, Table 2)`.
- **Theory and algorithms:** State a 1–2 sentence *Informal Theorem* early where readers need the intuition. State every assumption with one sentence on why it is reasonable and where it breaks. Follow each theorem with a short *Proof sketch* naming the key reduction or decomposition, and place complete proofs in the Appendix. Present algorithms in clean pseudocode boxes (`algorithm`/`algorithmic`), never raw Python/C++ source listings (`lstlisting`/`verbatim`).
- **Results prose (explain *why*, never dump logs):**
  - Open each subsection or paragraph with the **phenomenon** (*"Sequential stopping drives the compute savings, whereas prior heterogeneity alone adds little"*), cite the 2–3 numbers from the table/figure that establish it, and explain the mechanism.
  - Do **not** restate every row and confidence interval of a table inside parenthetical walls of text—the table is right beside the prose.
  - Always include at least one **cost / trade-off** comparison (accuracy vs. samples, FLOPs, latency, memory, or failure rate), not accuracy tables alone.
  - Report negative results and boundary conditions plainly and confidently once, where they inform the reader, without apologetic hedges or repetitive disclaimers.
- **Captions:** Start every figure caption with a **bold one-sentence takeaway** stating what the figure shows (*"**Figure 3. Calibration matters on weak verifiers and saturates on strong ones.**"*), never a label like *"Overview of our results."* Define every symbol, error-bar meaning (e.g., 95% bootstrap CI over $n$ problems), and color/marker convention in the figure or its caption.

## 3. Figure skills and the zero-collision rule

Pick the right specialized skill and read its `SKILL.md` before drawing:

| Need | Skill to use | Mandatory check |
|---|---|---|
| Teaser, architecture, pipeline, module zoom-in | `method-figure` (inspect `assets/gallery/` and `assets/gallery/venues/INDEX.md`; start from `templates/*.yaml`) | `scripts/validate.py` + `scripts/audit_pdf.py` (0 FAIL) |
| Empirical data plots (CS, ML, natural sciences) | `scipilot-figure` + `scientific-visualization` | `scipilot-figure/scripts/check_figure.py --strict` + bbox collision check |
| Economics, finance, management, forest/coef plots | `econ-figure` | `econ-figure/scripts/audit.py` (0 FAIL) |
| Editable journal SVG schematics / graphical abstracts | `academic-figures` | Headless Chromium `getBBox()` overlap check |
| Formulating theorems, separations & theory plots | `theory-tricks` | Overlay theoretical prediction (dashed) on measured points |

**Hard visual rules for every data plot and diagram:**
1. **Exact target width, zero LaTeX rescaling:** Create each figure at its exact LaTeX target width (`\columnwidth` or `\textwidth`, e.g., ICML `3.25in` / `6.75in`, NeurIPS `5.5in`, ACL `3.04in` / `6.32in`, CVPR `3.29in` / `6.90in`) and include with `\includegraphics{...}` at 1:1 scale so 7.5–8.5pt serif type never shrinks below the 6pt legibility floor.
2. **TrueType / OpenType embedding only:** In Matplotlib, always set `mpl.rcParams['pdf.fonttype'] = 42` and `mpl.rcParams['ps.fonttype'] = 42` so `pdffonts` reports zero Type 3 fonts.
3. **Unified semantic palette + grayscale redundancy:** One color = one meaning across every figure in the paper (`\ours` in warm orange `#D55E00` / Office warm accent, primary baselines in neutral slate/grey, variants/ablations in muted blue/teal), paired with distinct markers or line styles so every series remains distinguishable in grayscale. Remove top and right axes spines.
4. **Programmatic legend & label collision check (zero overlaps):** Never drop `ax.legend()` inside a crowded plot box where it can cross a curve, error band, or reference line. Prefer direct curve labels or a shared horizontal legend strip above the subplots (`bbox_to_anchor=(0.5, 1.02), loc='lower center', frameon=False`). Before `savefig`, run `fig.canvas.draw()` and verify via `get_window_extent()` that legend and text bounding boxes do not overlap plotted data segments or neighbor labels.
5. **300-DPI zoomed quadrant inspection (9/10 Oral bar):** Run `tile_figures.py` on every figure PDF and read both the full PNG and its zoomed quadrants (`_q1`..`_q4`). Any overlapping text, legend touching a curve, clipped tick/subscript, disconnected arrowhead, cramped panel, or repetitive layout across figures must be **redrawn at the source script**—never left in place and excused as a "minor issue" in a build note.

## 4. Page budget, equations, and structured appendix

- **Equations:** Keep every display equation strictly inside its column (`aligned`, `split`, `multline`); never use `\resizebox` on math, and never let an equation number wrap onto a line of its own.
- **Exact body-page landing:** When a venue sets an $N$-page main-text limit (e.g., 8 pages before `References`), finish the Conclusion in the bottom quarter of page $N$ with both columns balanced—neither spilling 2–15 lines onto page $N+1$ nor leaving half of page $N$ blank. Move secondary ablations or proof details to the Appendix to hit page $N$ cleanly.
- **Structured, dense Appendix:** Keep the same two-column template and continuous page numbering after `References`:
  - Open the Appendix with a compact **Appendix Contents** roadmap listing Sections A, B, C… and what each contains;
  - **A. Notation and Symbols:** Clean grouped table (`Symbol | Meaning | Section`);
  - **B. Extended Related Work:** Thematic `\paragraph{...}` blocks contrasting `\ours` with each research thread;
  - **C. Proofs and Derivations:** Restate each theorem/proposition before its complete proof, ordered by lemma dependencies;
  - **D. Experimental Setup and Reproducibility:** Datasets, splits, calibration/hyperparameter rules, baseline configurations, compute budget;
  - **E. Additional Experimental Analyses:** Each subsection (`E.1`, `E.2`, …) makes one clear point with its table or multi-panel figure placed right beside it (`[t]` / `[htbp]`, pairing narrow tables side by side, and placing `\FloatBarrier` at section ends so interior appendix pages never sit >40% empty).
