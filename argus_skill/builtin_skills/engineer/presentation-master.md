---
name: "PPT Master for Presentations and Paper Figures (Argus adapter)"
description: "Use the installed hugohe3/ppt-master workflow for the research vertical's default LiveFigure-style procedural native-PPTX research-paper conceptual figure route, as well as editable PPTX decks and templates. It remains usable when image-2 is unavailable."
---

# PPT Master — Argus Adapter

This is a thin adapter to the **complete upstream PPT Master installation**, not
a summary or replacement workflow. Upstream is MIT-licensed and pinned by Argus
to commit `2e29f3d3cfc379c689b07027d0fa776b9ff79291`.

## When to use

For research-paper conceptual figures, use PPT Master as the renderer for the
LiveFigure-style default: evidence-bound semantic contract, same-domain
exemplar/style retrieval, multiple layout plans, procedural native objects,
rendered visual critique, and bounded source-level repair. Paper-facing uses
include conceptual figures, method overviews, architecture diagrams, teasers,
and graphical abstracts. The toolkit can produce these through
SVG/design-spec/PPTX routes without image-2 or another generative image backend.

Do not use PPT Master merely to restyle an ordinary statistical chart; data
figures use the research vertical's SciencePlots route. Use Draw.io,
FigureSpec, or Mermaid/Graphviz instead only for an explicitly documented
supporting exact-topology exception.

## Locate and load the real skill

```bash
PPT_MASTER_ROOT="${ARGUS_SKILL_HOME:-$HOME/.argus-skill}/tools/ppt-master"
SKILL_DIR="$PPT_MASTER_ROOT/skills/ppt-master"
"${ARGUS_SKILL_BIN:-argus-skill}" --ppt-master-status
```

The status command validates the pinned revision, required toolkit files, clean
checkout, and dependencies for the active Python. If it fails, stop and report
that the operator must run:

```bash
${ARGUS_SKILL_BIN:-argus-skill} --install-ppt-master
```

Do not silently clone, update, or replace the toolkit inside a mission.

Before doing any PPT Master work:

1. Read `$SKILL_DIR/SKILL.md`.
2. Read `$SKILL_DIR/workflows/routing.md`.
3. Select exactly one upstream top-level route.
4. Read only the selected route and documents it explicitly triggers.
5. Follow the upstream gates, commands, source ownership, and recovery pointers.

Upstream examples spell the interpreter as `python3`. Inside Argus, treat that
token as a placeholder and run every PPT Master script with the injected
framework interpreter instead:

```bash
"${ARGUS_SKILL_PYTHON:-python3}" "$SKILL_DIR/scripts/<script>.py" ...
```

Do not call bare `python` or `python3`: the host interpreter may not contain the
audited PPT Master dependencies even when `--ppt-master-status` passes.

The upstream skill, workflows, references, scripts, chart library, icon library,
and templates are authoritative. Do not reconstruct them from this adapter.

## Argus compatibility contract

- Run upstream scripts by absolute path under `$SKILL_DIR`; keep generated
  projects and user artifacts in the mission workdir, never inside the installed
  toolkit.
- Do not run upstream `update_repo.py`. Argus owns the audited revision through
  `argus-skill --install-ppt-master`.
- Upstream Strategist, Image_Generator, and Executor role switches are modes
  within the current Engineer mission. They do not replace Argus Manager,
  Planner, Engineer, or Reviewer and do not justify unmanaged subagents.
- The Manager remains the only operator-facing role. Honor upstream blocking
  confirmation gates. Proceed without a live confirmation only when the
  operator explicitly delegated those decisions, exactly as upstream permits;
  otherwise return a concrete blocked/confirmation request rather than choosing
  on the user's behalf.
- Keep upstream's native-editability contract: generated SVG page sources compile
  to DrawingML/native PowerPoint objects; never flatten a whole deck into slide
  screenshots.
- For paper figures, use a pure white `#ffffff` canvas by default and confine
  low-saturation colour to semantic modules. Do not use a dark or tinted
  full-canvas background unless the manuscript itself does.
- Preserve all source, route, design-spec, validation, and export artifacts so
  the Reviewer can audit the deck.
- Optional image generation still requires an actually configured upstream
  backend. Never copy secrets into project artifacts or fabricate image output.
- For paper-facing figures, the active research vertical's figure policy wins;
  PPT Master cannot bypass required paper provenance or review gates.

## Installed capability surface

The pinned toolkit provides all four upstream routes:

- Generate PPTX
- Create Template
- Fill Native PPTX
- Enhance Native PPTX

It also includes source conversion, project scaffolding, SVG quality checks,
editable SVG→PPTX conversion, native template filling, native enhancement,
transitions, animations, narration, image tooling, visual review, chart
templates, icon libraries, and design references.

## Completion evidence

Do not report success from script exit alone. Require the selected upstream
route's final artifacts and validators, plus a fresh visual inspection of the
exported PPTX or its rendered pages. State unsupported fonts, rendering
differences, skipped optional stages, and unconfigured image backends plainly.
