# Figure Sources

The current technical report uses six paper-facing figures:

1. `argus_teaser.pdf` — first-page system and result overview;
2. `horizon_mountain.pdf` — recurrent roles, Stage dynamics, and training vision;
3. `swebench_evolution.pdf` — SWE-Bench Pro result, longitudinal evolution, and Reviewer routing/recovery;
4. `erdos_vertical_trace.pdf` — representative mathematical campaign;
5. `paper_case_study.pdf` — recurrent production mechanism and six scientific outputs;
6. `paper_case_trajectory.pdf` — a role-resolved 163.6-hour paper campaign.

Editable sources are retained alongside the exports:

- editable PowerPoint plus native SVG source for the teaser;
- HTML/SVG sources for the horizon and mathematical trace;
- PowerPoint source for the unified SWE-Bench Pro result/evolution/Reviewer figure;
- HTML/CSS/SVG sources generated from deterministic public data for the
  paper-production case study;
- real first-page thumbnails for the six autonomous paper outputs under
  `assets/paper_thumbnails/`;
- deterministic data files under `../evidence/`.

Additional PNGs and metadata sidecars in this directory are public legacy project
graphics referenced by the repository README or compatibility tests. They are not
figures in the current paper.

## Visual standard

- All six figures share one restrained anime-editorial system derived from the
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
