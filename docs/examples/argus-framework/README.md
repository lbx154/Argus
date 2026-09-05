# Argus framework: real SVG component example

![Argus framework](architecture.png)

Argus separates campaign authority and task planning from bounded execution
and evidence review. The Manager commits intent and stage policy; the Planner
defines tasks and dependencies; LifeSupervisor dispatches durable missions.
The Engineer produces code and artifacts for the Reviewer, whose outcomes feed
campaign scheduling and whose `continue` decision repairs the current mission.
Skill/Wiki memory, vertical contracts, runner backends, and durable records
support this workflow. Research requires independent review; other policies
can permit bounded self-review. The three numbered task nodes illustrate a
dependency graph, rather than a fixed number of tasks.

Solid blue arrows show task/evidence flow; dashed purple arrows show selected
policy and memory interfaces; the amber return arrow shows mission repair.
The drawing groups logical responsibilities rather than tracing Python calls.
The bottom band contains shared facilities, not additional sequential agents.

The active Engineer authored this SVG after running the component's `brief`
command on the current code and manuscript. No separate image-model call was
made. The Research Paper SVG/PDF component appears only as an on-demand facility.

## Source mapping

Implementation baseline: `6c14b4262869c8370eddabdfc7da0e1bb5752eee`, with the
local Research SVG component added.

| Figure area | Source files, relative to the repository |
| --- | --- |
| Manager authority | `argus_skill/manager/_core.py`, `argus_skill/manager/_stage_ops.py`, `argus_skill/manager/_vertical_ops.py` |
| Planner and campaign scheduling | `argus_skill/planner/planner.py`, `argus_skill/life/supervisor/_core.py` |
| Engineer and Reviewer | `argus_skill/loop.py`, `argus_skill/engineer/runner.py`, `argus_skill/reviewer/_core.py` |
| Skills and Wiki | `argus_skill/skills/loop_skill_library.py`, `argus_skill/manager/skill_review.py`, `argus_skill/wiki/context.py` |
| Research contracts and drawing | `argus_skill/verticals/research/stages.py`, `argus_skill/verticals/research/pipeline_figure.py` |
| Manuscript context | `technical_report/sections/04_argus_method.tex` |

Current code takes precedence where the technical report describes an older
implementation. No retired L3 critic or independent matcher/distiller is drawn.

## Render

From the repository root, with Chromium and Times New Roman installed:

```bash
python -m argus_skill.verticals.research.pipeline_figure render \
  --input docs/examples/argus-framework/architecture.source.svg \
  --output docs/examples/argus-framework/architecture.svg --pdf --png --width 624
```

Use [architecture.pdf](architecture.pdf) in the paper and edit
[architecture.source.svg](architecture.source.svg) for revisions. The PDF is
6.5 inches wide, with a minimum label size above 8 pt. Include it after
Introduction, preferably at the top of page 2 or 3; check the compiled paper's
actual floats without redrawing the figure to change its placement.
