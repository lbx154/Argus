# Figure Sources

The current technical report uses seven paper-facing figures:

1. `argus_teaser.pdf` — first-page system and result overview;
2. `horizon_mountain.pdf` — recurrent roles, Stage dynamics, and training vision;
3. `swebench_evolution.pdf` — SWE-Bench Pro comparison and longitudinal analysis;
4. `reviewer_mechanism.pdf` — Reviewer routing and recovery;
5. `erdos_vertical_trace.pdf` — representative mathematical campaign;
6. `paper_case_study.pdf` — recurrent production mechanism and six scientific outputs;
7. `paper_case_trajectory.pdf` — a role-resolved 163.6-hour paper campaign.

Editable sources are retained alongside the exports:

- HTML/SVG sources for the teaser, horizon, and mathematical trace;
- PowerPoint sources for the SWE-Bench Pro and Reviewer figures;
- HTML/CSS/SVG sources generated from deterministic public data for the
  paper-production case study;
- deterministic data files under `../evidence/`.

Additional PNGs and metadata sidecars in this directory are public legacy project
graphics referenced by the repository README or compatibility tests. They are not
figures in the current paper.

## Visual standard

- Paper-facing canvases use a 12-inch source width and are placed at full text
  width. Standard rendered height is approximately 3.1--4.3 inches; unusually tall
  diagrams should be compressed or split before inclusion.
- System and analysis figures use a white background, Argus blue (`#315BCE`) as the
  primary accent, neutral gray borders, and fixed role colors: Manager red, Planner
  blue, Engineer gold, and Reviewer teal.
- Panel titles use the `(a)`, `(b)` convention, rounded panels use restrained
  8--11 px corner radii, and shadows or gradients are avoided unless they encode a
  real distinction.
- Quantitative figures retain task-native units. Illustrative figures may use a
  different visual language, but captions must identify them as schematic.
