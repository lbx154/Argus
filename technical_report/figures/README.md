# Figure Sources

The current technical report uses seven paper-facing figures:

1. `argus_teaser.pdf` — first-page system and result overview;
2. `horizon_mountain.pdf` — recurrent roles, Stage dynamics, and training vision;
3. `swebench_evolution.pdf` — SWE-Bench Pro outcome, review summary, and longitudinal evolution;
4. `reviewer_mechanism.pdf` — adaptive Reviewer routing and revision recovery;
5. `erdos_vertical_trace.pdf` — representative mathematical campaign;
6. `paper_case_study.pdf` — recurrent production mechanism and six scientific outputs;
7. `paper_case_trajectory.pdf` — a role-resolved 163.6-hour paper campaign.

Editable sources are retained alongside the exports:

- the current teaser was supplied as a paper-facing vector PDF; the retained
  PowerPoint and SVG are the preceding editable version;
- component-editable PowerPoint sources for the long-horizon model, unified
  SWE-Bench Pro figure, Reviewer routing/recovery figure, mathematical trace,
  six-paper portfolio, and representative paper trajectory;
- HTML/CSS/SVG generation sources remain available for provenance and
  deterministic comparison;
- real first-page thumbnails for the six autonomous paper outputs under
  `assets/paper_thumbnails/`;
- deterministic data files under `../evidence/`.

Additional PNGs and metadata sidecars in this directory are public legacy project
graphics referenced by the repository README or compatibility tests. They are not
figures in the current paper.

## Visual standard

- All seven figures share one restrained editorial system derived from the
  mountain illustration: cream paper (`#FBF7EE`), dark navy linework (`#24465D`),
  low-saturation landscape colors, and the same Manager, Planner, Engineer, and
  Reviewer character assets under `assets/anime/`.
- Role accents stay within the same constrained navy, blue, teal, and gold family.
  Color communicates role or state; labels and geometry remain independently readable.
- Quantitative marks, axes, formulas, and reported values remain deterministic
  vector overlays. Anime artwork supplies visual continuity and narrative cues; it
  never encodes a measurement.
- Figure interiors use labels, symbols, and key numbers only. Explanatory sentences,
  protocol qualifications, and interpretation belong in the LaTeX caption or body.
- All retained figure text must remain readable after placement at manuscript width;
  duplicate titles, subtitles, footnotes, and prose callouts are removed.
- Paper-facing canvases remain full text width, with approximately 3.1--4.3 inches
  of rendered height. Panel labels use `(a)`, `(b)` where they clarify distinct
  claims, and captions distinguish measured panels from conceptual illustrations.
