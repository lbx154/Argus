# Argus Technical Report PPT Sources

Package date: 2026-07-20

Source report:

- Overleaf migration commit: `83e9be7`
- Upstream Argus baseline: `1de4cf49`
- Current Figure 1--7 refresh bundle: `/data/yijia/HarnessOpt/tmp/tu.zip`
  (integrated 2026-07-20)

## Native PowerPoint Sources

| Packaged filename | Original filename | Report usage |
| --- | --- | --- |
| `01_Figure1_Argus_Runtime_and_Benchmark_Overview.pptx` | `figures/argus_teaser.pptx` | Previous editable Figure 1 source; the current paper-facing Figure 1 was supplied as `pdf/01_Figure1_Argus_Runtime_and_Benchmark_Overview.pdf` |
| `02_Figure3_SWE-Bench_Evolution_and_Reviewer_Recovery.pptx` | `figures/swebench_evolution.pptx` | Directly used to build Figure 3 (`swebench_evolution.pdf`) |
| `03_Supplement_Reviewer_Revision_and_Recovery_Mechanism.pptx` | `figures/reviewer_mechanism.pptx` | Directly used for the standalone Reviewer routing and recovery figure |

## Component-Editable PowerPoint Sources for Figures 2, 5, 6, and 7

The requested Figure 2, Figure 5, Figure 6, and Figure 7 decks are under
`figures_2_5_6_7/`:

- `Figure_2_Long_Horizon_Runtime_Model.pptx`
- `Figure_5_Mathematical_Vertical_Trace.pptx`
- `Figure_6_Six_Project_Paper_Portfolio.pptx`
- `Figure_7_Paper_Production_Trajectory.pptx`

All four decks are rebuilt with native PowerPoint components. Backgrounds,
thumbnails, and role portraits are separate pictures; cards, text, bars, Stage
nodes, paths, and connectors are separate editable objects. None of these four
decks contains a whole-figure SVG overlay.

The PPTX-only archive is:

`Argus_Figures_2_5_6_7_Component_Editable_PPTX_2026-07-20.zip`

The complete source archive, including Figure 1's supplied PDF and all retained
PowerPoint sources, is:

`Argus_Technical_Report_PPT_Sources_2026-07-20.zip`
