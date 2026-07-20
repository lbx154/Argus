---
name: PPT Master for Presentations and Paper Figures (Argus adapter)
description: Use the installed hugohe3/ppt-master workflow for polished editable research-paper conceptual, method, architecture, teaser, and graphical-abstract figures as well as PPTX decks and templates. This is a first-class deterministic visual route when image-2 is unavailable; it does not require a generative image model.
category: visual-communication
version: 3
created_at: 2026-07-19T00:00:00+00:00
---

# PPT Master — Argus Adapter

This is a thin adapter to the **complete upstream PPT Master installation**, not
a summary or replacement workflow. Upstream is MIT-licensed and pinned by Argus
to commit `2e29f3d3cfc379c689b07027d0fa776b9ff79291`.

## When to use

Use PPT Master when a paper or presentation needs deliberate visual hierarchy,
editable composition, icons, callouts, grouped modules, or richer layout than a
simple box-and-arrow renderer provides. Paper-facing uses include conceptual
figures, method overviews, architecture diagrams, teasers, and graphical
abstracts. The toolkit can produce these through SVG/design-spec/PPTX routes
without image-2 or another generative image backend.

Do not use PPT Master merely to restyle an ordinary statistical chart. Use the
research visualization router to compare it with HTML/SVG, ECharts/Recharts,
FigureSpec, and other installed deterministic routes before rendering.

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
