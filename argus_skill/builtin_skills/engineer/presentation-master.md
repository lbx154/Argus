---
name: Presentation Master
description: Build or revise an evidence-grounded, editable PPTX for a research talk, technical review, product launch, or executive briefing. Use when the deliverable is a slide deck, presentation, keynote, pitch, or PPT/PPTX. Covers narrative planning, template-safe authoring, editable charts and diagrams, rendering, and slide-by-slide visual QA.
category: visual-communication
version: 1
created_at: 2026-07-19T00:00:00+00:00
---

# Presentation Master

Produce a deck that is truthful, legible in the room, and editable after handoff.
The `.pptx` is not complete until every slide has been rendered and inspected.

Adapted as an original Argus workflow from the MIT-licensed
[`hugohe3/ppt-master`](https://github.com/hugohe3/ppt-master/tree/2e29f3d3cfc379c689b07027d0fa776b9ff79291/skills/ppt-master)
and OpenAI's MIT-licensed
[`google-slides` skill](https://github.com/openai/plugins/tree/11c74d6ba24d3a6d48f54a194cd00ef3beea18f9/plugins/google-drive/skills/google-slides).
Do not import Anthropic's `pptx` skill: its skill-specific license forbids reuse
outside Anthropic services.

## Route before authoring

Choose one route and record it in `slides/BRIEF.md`:

| Situation | Route |
|---|---|
| New deck with no house template | Generate editable PPTX from a local build script |
| Existing template or prior deck supplied | Preserve its masters, dimensions, theme, and reusable layouts |
| Existing deck needs revision | Patch only requested slides; retain untouched object IDs and speaker notes |
| User explicitly wants Google Slides | Build/import a native deck only when the required connector is available |

Never flatten a whole slide into a screenshot. Raster images are allowed as
photos, textures, or generated illustrations; titles, body text, charts, tables,
and ordinary shapes must remain editable.

## Required artifacts

Keep the deck reproducible:

```text
slides/
  BRIEF.md                 # audience, duration, purpose, sources, constraints
  SLIDE_PLAN.json          # ordered slide claims and evidence
  DESIGN_CONTRACT.md       # typography, palette, grid, recurring components
  build.{mjs,js,py}        # source of truth for a generated deck
  assets/                  # licensed/source-attributed inputs
  output.pptx
  rendered/                # one fresh image per slide
  QA.md                    # inspected defects and disposition
```

For an edited native template, the input deck may be the source of truth instead
of a full rebuild script, but keep a small deterministic patch script and an
untouched original.

## Workflow

1. **Ground the content.** Read the supplied source material and build a source
   table in `BRIEF.md`. Every number, comparison, quotation, and status claim
   needs a real source. Mark unknowns; never invent data to fill a layout.
2. **Plan the talk, not pages.** Set audience, decision or learning objective,
   duration, and expected Q&A. Budget roughly one substantive slide per minute,
   then adjust for demos and dense technical explanations.
3. **Write `SLIDE_PLAN.json`.** Each slide declares:
   `id`, `takeaway`, `archetype`, `evidence_refs`, `visual`, and optional
   `speaker_notes`. One slide should make one defensible point.
4. **Select varied archetypes intentionally.** Use title, section divider,
   single-claim visual, comparison, process, evidence chart, architecture,
   demo, decision, and closing/action slides. Do not repeat a title-plus-six-
   bullets layout across the deck.
5. **Freeze a design contract before polishing.** Record aspect ratio, safe
   margins, grid, type scale, palette, chart colors, image treatment, and footer
   behavior. Minimum body text is normally 18 pt; use larger type for projected
   talks. Color is never the only carrier of meaning.
6. **Author editable objects.** Prefer a project-local Node toolchain with
   `pptxgenjs` for generated decks or `python-pptx` for bounded native-template
   edits. Install in the project, not the Argus harness. Reuse the `Mermaid and
   Graphviz Diagrams`, `Draw.io Diagram Authoring`, FigureSpec, paper chart, or
   image-generation skill for visual assets according to the requested output.
7. **Render the actual PPTX.** Use LibreOffice or another available office
   renderer to produce PDF, then render every PDF page to an image. A successful
   library call is not visual evidence.
8. **Inspect every fresh thumbnail.** Check clipping, overlap, off-canvas
   objects, tiny text, broken fonts, stretched images, stale placeholders,
   inconsistent alignment, low contrast, connector mistakes, and unsupported
   glyphs. Re-render every touched slide after a fix.
9. **Run content QA.** Confirm slide order, citations, units, chart scales,
   speaker notes, appendix/backups, and that the closing slide states the actual
   conclusion or next action rather than a generic "Thank you".
10. **Deliver source plus evidence.** Return the editable `.pptx`, build/patch
    source, required assets, and `QA.md`. State any renderer differences or
    unverified animation behavior plainly.

## Practical commands

Use available equivalents when these tools are absent:

```bash
# Generated route
npm install --save-dev pptxgenjs
node slides/build.mjs

# Native-template patch route
python -m pip install python-pptx
python slides/patch.py

# Fresh render for inspection
libreoffice --headless --convert-to pdf --outdir slides/rendered slides/output.pptx
pdftoppm -png -r 144 slides/rendered/output.pdf slides/rendered/slide
```

Do not silently substitute a different template, aspect ratio, or export format
when a command is unavailable. Surface the missing capability and choose a
traceable alternative.

## Acceptance checklist

- The deck opens and has the requested slide dimensions.
- All slide claims trace to supplied or cited evidence.
- Text, charts, tables, and basic shapes remain editable.
- No placeholders, clipping, overlap, or off-canvas content remain.
- Every slide was inspected from a fresh render of the final PPTX.
- Typography, palette, spacing, and visual grammar are coherent.
- Source files and licenses for external assets are recorded.
- The deck fits the promised duration and audience.

## Boundaries

- For paper-facing figures, obey the active research vertical's figure policy;
  this skill cannot bypass required image-2 provenance or paper checklists.
- Never fabricate customer logos, benchmark results, citations, or quotations.
- Do not claim animations or transitions were verified unless the target
  presentation application was actually used to inspect them.
