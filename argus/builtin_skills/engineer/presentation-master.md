---
name: "Editable Presentations with PPT Master"
description: "创建或编辑可编辑的演示文稿、模板和原生 PPT 图。 Use the installed PPT Master toolkit for editable PPTX delivery: locate it, run its scripts through the supplied interpreter, keep objects native and verify the rendered export. Paper figure policy comes from the active research vertical."
---

# Editable Presentations with PPT Master

Use when the work needs editable PowerPoint slides, templates or native PPT
diagrams. This is the one place the toolkit's operating notes live; a vertical
skill that draws through PPT Master points here rather than repeating them.
Ordinary measured charts and Markdown diagrams can use simpler tools when that
satisfies the request.

For paper-facing figures the active research vertical decides the route:
method, framework and other conceptual figures go through PPT Master by its
figure studio's Method D; measured data charts go through its `paper_charts`
helper, not through slides. This adapter does not override that routing.

## Locate the supported toolkit

Argus manages a pinned PPT Master checkout. Locate it with
`python -m argus.tools.ppt_master status` (`argus --ppt-master-status` is the
same check; `path --skill-root` prints only the directory). The reported
`skill_root` holds the toolkit instructions, layout references,
`scripts/svg_quality_checker.py`, `scripts/svg_to_pptx.py` and
`scripts/pptx_to_svg.py`. A failed status check means a missing installation,
a dependency problem or a modified checkout: report the actual cause.
`argus --install-ppt-master` installs or repairs it only within existing
operator authorization. Do not clone, update or replace the shared toolkit on
your own, and do not run the upstream `update_repo.py`; Argus manages its
revision.

Read the toolkit's `SKILL.md` and its `workflows/routing.md`, then only the
selected route's references. Reuse the installed Generate PPTX, Create
Template, Fill Native PPTX or Enhance Native PPTX workflow instead of inventing
a second toolkit. Run toolkit scripts through the supplied interpreter:

```bash
SKILL_DIR=$(python -m argus.tools.ppt_master path --skill-root)
"${ARGUS_SKILL_PYTHON:-python3}" "$SKILL_DIR/scripts/<script>.py" ...
```

Do not call bare `python` or `python3`: the toolkit's dependency checks are
tied to the configured interpreter. Project dependencies still belong in the
project environment.

## Deliver and verify

- Keep projects and outputs in the authorized workdir, outside the installed
  toolkit.
- Follow the requested audience, format and available input evidence. Upstream
  role switches are workflow modes inside the current task, not permission to
  spawn agents.
- Carry forward decisions the operator already delegated. Request only an
  actually missing decision or resource, with a concrete draft where possible.
- Preserve native text, shapes and connectors required for editability; a
  single pasted screenshot is not an editable slide or diagram.
- Export and inspect the actual rendered output. Check clipping, fonts, reading
  order, contrast and content against the source. Keep the editable final
  artifact and the necessary source; do not claim success from a script exit
  code alone. If previews come from SVG rather than a PowerPoint render, say so
  rather than claiming an Office rendering was inspected.
- Use a configured image backend only when the chosen route and request need
  it. Follow the existing permitted fallback when it does not; never fabricate
  image output.

## Conversion details

Native theme faces such as `+mn-lt` and `+mj-lt` can resolve to a different
font from the authoring SVG; inspect the actual theme and its glyph coverage,
and position ordinary letters at real subscript and superscript baselines when
modifier-letter glyphs are absent. For a short multiline label, use separate
native text objects with explicit baselines when the converter does not
preserve line breaks; a merged line or an automatic wrap must not clip the
output or alter its notation.

Report material unsupported features or rendering differences. Stop when the
requested artifact and the decisive visual and editability checks are
satisfied.
