---
name: Mermaid and Graphviz Diagrams
description: Create source-controlled, reproducible flowcharts, sequence diagrams, state machines, dependency graphs, ER diagrams, and lightweight architecture views using Mermaid or Graphviz, then render and inspect the real output. Use for Markdown-native diagrams, DOT graphs, dependency visualization, or documentation diagrams.
category: visual-communication
version: 1
created_at: 2026-07-19T00:00:00+00:00
---

# Mermaid and Graphviz Diagrams

Use text diagrams when the topology matters more than illustration. The source
file is authoritative; the rendered SVG/PDF is validation evidence.

Adapted as an original Argus workflow from the MIT-licensed ARIS
[`mermaid-diagram` skill](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep/blob/c5f3d5bfc694a812012729841e9697223e4f2130/skills/mermaid-diagram/SKILL.md)
and GitHub's MIT-licensed
[`diagram conventions`](https://github.com/github/awesome-copilot/blob/26fe2d126bf79aafb38f43344d450b69632200f8/skills/threat-model-analyst/references/diagram-conventions.md).

## Pick the correct format

| Need | Use |
|---|---|
| GitHub/Markdown-native flow, sequence, state, class, ER, journey, or timeline | Mermaid |
| Large dependency graph, hierarchy, call graph, or layout-engine control | Graphviz DOT |
| Exact publication SVG geometry and deterministic paper layout | FigureSpec |
| Interactive hand editing, rich shape libraries, or `.drawio` delivery | Draw.io |
| Painterly teaser or conceptual paper illustration | The active image-generation skill |

Do not use Mermaid or DOT merely because it is quick when the requested
deliverable is an editable presentation or publication-specific figure.

## Workflow

1. **Write the communication contract.** State audience, one intended takeaway,
   diagram type, scope boundary, and authoritative sources.
2. **Extract semantics before layout.** List nodes, edges, direction, edge
   labels, groups/trust boundaries, and unresolved relationships. Every
   load-bearing edge must be supported by code, data, documentation, or an
   explicit user statement. Mark hypotheses as hypotheses.
3. **Choose a stable direction.** Prefer left-to-right for pipelines and
   top-to-bottom for hierarchies. Keep one dominant flow; avoid gratuitous
   feedback loops and crossing edges.
4. **Author the source.** Store `.mmd` or `.dot` beside a short
   `<name>.sources.md` mapping important nodes and edges to evidence.
5. **Render with the real engine.** Syntax inspection by eye is insufficient.
6. **Inspect the SVG/PDF.** Check labels, arrow direction, line crossings,
   boundary containment, clipped text, contrast, grayscale meaning, and
   readability at intended size.
7. **Fix source and re-render.** Never patch generated SVG as the primary fix.
8. **Deliver source and render.** Include the exact command and tool version.

## Rendering

```bash
# Mermaid: use a project-local package when possible
npx --yes @mermaid-js/mermaid-cli \
  -i docs/architecture.mmd -o docs/architecture.svg -b transparent

# Graphviz
dot -Tsvg docs/dependencies.dot -o docs/dependencies.svg
dot -Tpdf docs/dependencies.dot -o docs/dependencies.pdf
```

Choose the Graphviz engine intentionally: `dot` for directed hierarchies,
`neato`/`fdp` for relationship networks, and `sfdp` for large graphs. Record the
choice; layout-engine changes can materially alter the diagram.

## Visual and semantic rules

- Put connectors behind nodes and attach them to real endpoints.
- Use short labels; move explanations into adjacent prose.
- Use grouping only for a real ownership, trust, deployment, or lifecycle
  boundary.
- Use a colorblind-safe palette and redundant shape/line/label encoding.
- Distinguish architecture, runtime sequence, and data-flow diagrams; do not
  merge incompatible views into one unreadable canvas.
- Keep diagram source deterministic: stable IDs, stable ordering, no timestamps.
- For security/data-flow diagrams, identify external actors, trust boundaries,
  stores, processes, and direction explicitly.

## Acceptance evidence

- Renderer exits successfully with no syntax error.
- Rendered output exists and is non-empty.
- All declared nodes appear; all important edges point to the intended target.
- No unsupported claim was introduced to make the diagram look complete.
- The final render is legible at its actual use size and without relying on
  color alone.
- Source, render, evidence map, command, and versions are retained.

## Boundaries

For paper-facing non-data figures, follow the active research vertical's figure
and provenance rules. A Mermaid or DOT render does not satisfy an image-2 gate
unless that policy explicitly allows it.
