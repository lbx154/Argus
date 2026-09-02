---
name: "Draw.io Diagram Authoring"
description: "Author or revise editable Draw.io diagrams for architecture, UML, ER, network, BPMN, sequence, and process deliverables. Use when the user requests a .drawio file, needs interactive post-handoff editing, or needs rich standard shape libraries beyond Mermaid and Graphviz."
---

# Draw.io Diagram Authoring

Produce an editable `.drawio` source plus a fresh SVG or PDF render. XML that
parses is not enough: the final diagram must also be visually inspected.

Adapted as an original Argus workflow from GitHub's MIT-licensed
[`draw-io-diagram-generator` skill](https://github.com/github/awesome-copilot/tree/26fe2d126bf79aafb38f43344d450b69632200f8/skills/draw-io-diagram-generator).

## When to use

- The requested deliverable is `.drawio`.
- A human needs to rearrange or extend the diagram after handoff.
- UML, ER, BPMN, cloud, network, or other Draw.io shape libraries materially
  improve the result.
- A multipage diagram belongs in one editable file.

For Markdown-native docs use Mermaid; for large graph layout use Graphviz; for
deterministic paper SVG use FigureSpec. Do not add Draw.io when a simpler
source-controlled representation is sufficient.

## Source contract

Prefer uncompressed mxGraph XML so diffs remain inspectable:

```xml
<mxfile host="app.diagrams.net">
  <diagram id="architecture" name="Architecture">
    <mxGraphModel>
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <!-- vertices and edges -->
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

Use stable semantic IDs, not regenerated random IDs. Every vertex and edge must
have a valid parent; each edge must reference existing source and target cells.
Keep geometry explicit and deterministic.

## Workflow

1. **Define the view.** Record audience, one takeaway, diagram family, scope,
   sources, and desired page size.
2. **List the semantic content.** List nodes, relationships, groups, edge
   direction, and uncertainty before assigning coordinates.
3. **Choose a layout.** Use a grid and one dominant flow. Reserve whitespace for
   labels and future edits. Separate deployment, control flow, data flow, and
   sequence views into pages rather than mixing them.
4. **Author editable XML.** Use native vertices, edges, groups, and shape-library
   styles. Do not hide a flattened screenshot beneath a few editable labels.
5. **Validate mechanically.** Parse the XML; check the `mxfile`/`diagram`/
   `mxGraphModel` structure, unique IDs, parent references, edge endpoints, and
   geometry.
6. **Open or render with Draw.io.** Use the desktop/CLI export when available.
   If it is unavailable, say so; XML validation alone is not visual validation.
7. **Inspect the fresh render.** Check clipping, overlap, edge routing, line
   crossings, z-order, container boundaries, font fallback, contrast, and
   intended page bounds.
8. **Revise source, then re-export.** Never make the exported SVG the only fixed
   copy.

## Mechanical validation

Use a project-local validator or this minimum parser check:

```bash
python - <<'PY'
from pathlib import Path
from xml.etree import ElementTree as ET

path = Path("docs/architecture.drawio")
root = ET.parse(path).getroot()
assert root.tag == "mxfile"
diagrams = root.findall("diagram")
assert diagrams
for diagram in diagrams:
    model = diagram.find("mxGraphModel")
    assert model is not None and model.find("root") is not None
print(f"parsed {len(diagrams)} page(s): {path}")
PY
```

When the Draw.io CLI is installed:

```bash
drawio --export --format svg --output docs/architecture.svg docs/architecture.drawio
drawio --export --format pdf --output docs/architecture.pdf docs/architecture.drawio
```

## Design rules

- Connect edges to nodes, never to approximate canvas coordinates.
- Keep connectors behind nodes; keep labels above connectors.
- Use consistent shape semantics across pages.
- Encode state or risk with text/icon/line style as well as color.
- Use containers only for real boundaries such as service ownership, network
  zones, lifecycle phases, or trust domains.
- Avoid crossings; if unavoidable, use waypoints and make the crossing
  visually unambiguous.
- Preserve source order, IDs, page names, and geometry where possible when
  revising an existing file.

## Acceptance evidence

- `.drawio` parses and opens in a compatible editor.
- IDs are unique; parent, source, and target references resolve.
- A fresh exported SVG/PDF was visually inspected.
- Important nodes and edges trace to authoritative inputs.
- The source remains genuinely editable and is delivered with the render.
- External icons, fonts, and assets have recorded licenses.

## Boundary

For paper-facing figures, the active research vertical's figure policy wins.
Draw.io is not a shortcut around required image-generation provenance or final
paper review.
