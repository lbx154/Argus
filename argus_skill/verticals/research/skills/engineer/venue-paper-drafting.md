---
name: "Venue Paper Drafting"
description: "Create a complete paper draft from the selected venue and its official author kit."
---

# Venue Paper Drafting

Use this only in Paper after Experiment clears the paper-entry bar. Read
`HANDOFF.md`, direct evidence, the selected venue in pipeline state, and that
venue's current official author kit.

## Select and package the evidence

Keep the existing paper-facing requirements: write a five-sentence abstract of
at least 170 words, use exact headline numbers in the major reader-facing
locations where they establish the claim, and give every figure and table
caption a numerical takeaway. Keep the complete method, baseline, control,
adverse-result, uncertainty, and scope coverage in the paper. These requirements
make the paper substantive; do not weaken them to make the draft feel lighter.

Before drafting prose, assign the complete evidence to five roles:

- **headline** directly establishes the thesis and may recur in the abstract,
  introduction, results, main caption, and conclusion;
- **mechanism** explains why the result occurs or fails;
- **disambiguating control** rules out a credible alternative explanation;
- **scope-changing** changes the claim, uncertainty, population, or boundary;
- **completeness** makes the comparison comprehensive or reproducible without
  changing the current inference.

For each item, choose its canonical full location and any repeat locations.
Methods and tables retain complete definitions and matrices; prose selects the
rows, columns, differences, and controls that change the current inference and
explains why. Appendix placement is packaging, not deletion. Adverse or null
results that change the headline interpretation remain visible in the main
reader path.

The same exact headline number may appear in several major locations, but each
appearance must do that location's job: evidence in the abstract, expectation
change in the introduction, full conditional interpretation in results,
standalone readability in the caption, and scientific meaning in the
conclusion. Do not impose a universal repetition cap, and do not copy the same
method-by-dataset-by-metric recital into every location.

## Draft

- Build one confident thesis around the method's strongest supported win.
- Follow the selected venue's expected reader path and required end matter.
- Explain the real mechanism and actual evaluated implementation.
- Compare against real strong published baselines under fair information and
  resource conditions.
- Include every intended claim-bearing experiment, figure, table, and citation.
- Keep internal paths, role names, workflow language, and development history
  out of the manuscript.
- Do not write a negative-result paper or experiment chronology.

Every figure and table must carry a scientific claim. Figure 1 should explain
the method or central mechanism. Table 1 should normally present the main
quantitative result. For a method pipeline, open `research-svg-pipeline.md` and
draw the current code and paper as a compact, horizontal, staggered SVG with
Times New Roman; include its vector PDF export. Use readable publication-scale typography and conventional
axes, units, captions, and uncertainty. Package a caption as the question the
figure answers, the necessary comparison conditions, the decisive exact number,
and what that number establishes. The visual carries the complete matrix; the
caption identifies its reader-facing structure rather than reading every cell
aloud.

Maintain only `paper/main.tex`, its direct included sources, bibliography,
figures, tables, rendered paper, and project-root `HANDOFF.md`. Compile under
the official template, then enter Review. Scientific acceptance, strict visual
inspection, and academic-language polishing happen only there.
