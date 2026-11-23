# SVG Generation Guide for Academic Figures

This reference contains Python code patterns and SVG component templates for generating publication-quality scientific figures.

---

## SVG Generation Architecture

### Method: Python String Templating

Use Python to generate SVG XML. This approach gives full control over coordinates, allows parameterized layouts, and produces clean, editable SVG.

```python
def generate_figure_svg(config):
    """
    Main figure generator.
    config = {
        'width_mm': 183,
        'height_mm': 120,
        'panels': [...],
        'arrows': [...],
        'title': None,
        'journal': 'nature'
    }
    """
    svg_parts = []
    svg_parts.append(svg_header(config['width_mm'], config['height_mm']))
    svg_parts.append(svg_defs())  # arrows, gradients, patterns

    for panel in config['panels']:
        svg_parts.append(render_panel(panel))

    for arrow in config['arrows']:
        svg_parts.append(render_arrow(arrow))

    svg_parts.append(svg_footer())
    return '\n'.join(svg_parts)
```

---

## Core SVG Templates

### SVG Header with Inkscape Compatibility

```python
def svg_header(width_mm, height_mm):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
     width="{width_mm}mm" height="{height_mm}mm"
     viewBox="0 0 {width_mm} {height_mm}"
     version="1.1">
  <sodipodi:namedview
     inkscape:document-units="mm"
     units="mm" />
'''
```

### Standard Defs Block (Arrows, Patterns)

```python
def svg_defs():
    return '''  <defs>
    <!-- Standard filled arrow -->
    <marker id="arrow-std" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="4" markerHeight="4" orient="auto-start-reverse"
            markerUnits="strokeWidth">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#333333"/>
    </marker>

    <!-- Open arrow for annotations -->
    <marker id="arrow-open" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="3.5" markerHeight="3.5" orient="auto-start-reverse"
            markerUnits="strokeWidth">
      <path d="M 1 1 L 9 5 L 1 9" fill="none" stroke="#333333" stroke-width="1.5"
            stroke-linecap="round" stroke-linejoin="round"/>
    </marker>

    <!-- Bold workflow arrow -->
    <marker id="arrow-bold" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#0072B2"/>
    </marker>

    <!-- T-bar (inhibition) -->
    <marker id="tbar" viewBox="0 0 10 10" refX="5" refY="5"
            markerWidth="4" markerHeight="4" orient="auto">
      <line x1="5" y1="0" x2="5" y2="10" stroke="#333333" stroke-width="2"/>
    </marker>

    <!-- Dashed pattern for placeholders -->
    <pattern id="placeholder-hatch" patternUnits="userSpaceOnUse"
             width="3" height="3" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="3" stroke="#DDDDDD" stroke-width="0.5"/>
    </pattern>
  </defs>
'''
```

### SVG Footer

```python
def svg_footer():
    return '</svg>'
```

---

## Panel Components

### Panel Container (with label)

```python
# Layout constants — use these globally for consistent spacing
LABEL_OFFSET_Y = 2.5   # label baseline this far ABOVE the content area top
CONTENT_PAD_TOP = 1.0   # content starts this far below panel top
HEAD_ROOM = 7.0         # reserved above first panel row for labels

def R(val, decimals=1):
    """Round all coordinates to avoid floating-point noise in SVG."""
    return round(val, decimals)

def panel_label(label, content_x, content_y):
    """
    Place panel label ABOVE the content area.
    content_y is the top of the content area (where rects/schematics start).
    Label baseline sits at content_y - LABEL_OFFSET_Y (2.5mm above).
    """
    lx = R(content_x)
    ly = R(content_y - LABEL_OFFSET_Y)
    return f'''    <text x="{lx}" y="{ly}"
          font-family="Helvetica, Arial, sans-serif"
          font-size="3.5" font-weight="bold" fill="#000000">{label}</text>
'''
```

**CRITICAL**: The old pattern `panel_label(label, x, panel_top_y)` placed labels AT the panel boundary. The new pattern places them ABOVE it. When laying out panels, ensure `panel_top_y >= MARGIN + HEAD_ROOM` so labels have room.

### Plot Placeholder

```python
def plot_placeholder(x, y, w, h, description, subtitle="", panel_label=None):
    """
    Dashed rectangle indicating where a data plot should be imported.
    ALL text stays INSIDE the rect — especially the dimension annotation.
    """
    x, y, w, h = R(x), R(y), R(w), R(h)
    cx = R(x + w / 2)
    cy = R(y + h / 2)
    # Dimension text: INSIDE rect, 2mm from bottom-right corner
    dim_x = R(x + w - 2)
    dim_y = R(y + h - 2)

    return f'''    <g id="placeholder-{description.lower().replace(' ', '-')[:20]}">
      <rect x="{x}" y="{y}" width="{w}" height="{h}"
            fill="#F5F5F5" stroke="#999999" stroke-width="0.3"
            stroke-dasharray="2.5,2" rx="1.5" ry="1.5"/>
      <!-- Title: centered -->
      <text x="{cx}" y="{R(cy - 3)}"
            font-family="Helvetica, Arial, sans-serif" font-size="2.8"
            fill="#333333" text-anchor="middle" font-weight="bold">{description}</text>
      <!-- Subtitle -->
      <text x="{cx}" y="{R(cy + 1)}"
            font-family="Helvetica, Arial, sans-serif" font-size="2"
            fill="#888888" text-anchor="middle">{subtitle}</text>
      <!-- Import instruction -->
      <text x="{cx}" y="{R(cy + 5)}"
            font-family="Helvetica, Arial, sans-serif" font-size="1.6"
            fill="#888888" text-anchor="middle" font-style="italic">[Import plot here]</text>
      <!-- Dimension: INSIDE rect, bottom-right -->
      <text x="{dim_x}" y="{dim_y}"
            font-family="Helvetica, Arial, sans-serif" font-size="1.3"
            fill="#BBBBBB" text-anchor="end">{w:.0f}\u00d7{h:.0f} mm</text>
    </g>
'''
```

### Schematic Box (for processes, devices, components)

```python
def schematic_box(x, y, w, h, label, fill_color="#FFFFFF",
                  stroke_color="#333333", rx=1.5):
    """
    Rounded rectangle with centered label — the building block of schematics.
    """
    cx = x + w / 2
    cy = y + h / 2

    return f'''    <g id="box-{label.lower().replace(' ', '-')}">
      <rect x="{x}" y="{y}" width="{w}" height="{h}"
            fill="{fill_color}" stroke="{stroke_color}"
            stroke-width="0.4" rx="{rx}" ry="{rx}"/>
      <text x="{cx}" y="{cy + 0.9}"
            font-family="Helvetica, Arial, sans-serif" font-size="2.2"
            fill="#333333" text-anchor="middle">{label}</text>
    </g>
'''
```

### Workflow Step (numbered circle + label)

```python
def workflow_step(cx, cy, r, number, label, fill_color="#0072B2"):
    """
    Numbered circle with text label below — for process workflows.
    """
    return f'''    <g id="step-{number}">
      <circle cx="{cx}" cy="{cy}" r="{r}"
              fill="{fill_color}" stroke="none"/>
      <text x="{cx}" y="{cy + 1}"
            font-family="Helvetica, Arial, sans-serif" font-size="2.5"
            fill="#FFFFFF" text-anchor="middle" font-weight="bold">{number}</text>
      <text x="{cx}" y="{cy + r + 3.5}"
            font-family="Helvetica, Arial, sans-serif" font-size="2"
            fill="#333333" text-anchor="middle">{label}</text>
    </g>
'''
```

---

## Connector Components

### ⚠️ ARROW SPACING RULES (CRITICAL — READ BEFORE GENERATING ANY ARROWS)

**These rules prevent the most common visual defect in generated figures: cramped, overlapping arrows.**

1. **Minimum arrow length**: 10mm. NEVER draw an arrow shorter than 10mm.
2. **Start/end clearance**: Arrows start 3mm AFTER source panel edge, end 3mm BEFORE target panel edge.
3. **Minimum inter-panel gap for arrowed connections**: 18mm (= 3mm clearance + 12mm arrow + 3mm clearance).
4. **Label placement**: Labels go ABOVE horizontal arrows (y offset = -3mm from line). Labels go LEFT of vertical arrows (x offset = -3mm). Labels NEVER sit on the arrow line itself.
5. **Label clearance**: 3mm minimum between any label text and any other element (panel border, other text, other arrow).
6. **When there's no room**: If the gap is < 16mm, do NOT place an arrow. Either widen the gap or omit the arrow.

**Pre-flight check function** — call this BEFORE writing SVG to verify all arrows are valid:

```python
def validate_arrows(arrows, panels):
    """
    arrows: [{'x1': ..., 'y1': ..., 'x2': ..., 'y2': ..., 'label': ...}, ...]
    Returns list of warnings. If any warning, fix layout before generating SVG.
    """
    import math
    warnings = []
    for i, a in enumerate(arrows):
        length = math.sqrt((a['x2'] - a['x1'])**2 + (a['y2'] - a['y1'])**2)
        if length < 10:
            warnings.append(f"Arrow {i}: length {length:.1f}mm < 10mm minimum!")
        if length < 16 and a.get('label'):
            warnings.append(f"Arrow {i}: length {length:.1f}mm too short for label '{a['label']}'")
    return warnings
```

### Straight Arrow

```python
def straight_arrow(x1, y1, x2, y2, marker="arrow-std",
                   color="#333333", width=0.5):
    """
    Simple arrow. Caller MUST ensure (x2-x1) or (y2-y1) >= 10mm.
    """
    return f'''    <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"
          stroke="{color}" stroke-width="{width}"
          marker-end="url(#{marker})"/>
'''
```

### Curved Arrow (quadratic Bezier)

```python
def curved_arrow(x1, y1, cx, cy, x2, y2, marker="arrow-std",
                 color="#333333", width=0.5):
    """
    cx, cy: control point for the curve.
    The visual arc length will be longer than the straight-line distance,
    so even short straight-line gaps can work with curves.
    """
    return f'''    <path d="M {x1} {y1} Q {cx} {cy} {x2} {y2}"
          fill="none" stroke="{color}" stroke-width="{width}"
          marker-end="url(#{marker})"/>
'''
```

### Flow Arrow with Label (the workhorse connector)

```python
def flow_arrow(x1, y1, x2, y2, label=None, color="#0072B2",
               width=0.6, marker="arrow-bold"):
    """
    Bold arrow for workflow connections, optionally labeled.

    Label placement:
    - Horizontal arrows: label ABOVE the line, centered, y offset -3.5mm
    - Vertical arrows: label LEFT of line, centered vertically, x offset -3.5mm
    - Diagonal arrows: label above-left of midpoint
    """
    import math
    length = math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
    assert length >= 10, f"Arrow too short: {length:.1f}mm (min 10mm)"

    parts = [f'''    <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"
          stroke="{color}" stroke-width="{width}"
          marker-end="url(#{marker})"/>''']

    if label:
        mx = (x1 + x2) / 2
        my = (y1 + y2) / 2

        # Determine if mostly horizontal or mostly vertical
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        if dx >= dy:
            # Horizontal-ish: label above
            label_x = mx
            label_y = my - 3.5
            anchor = "middle"
        else:
            # Vertical-ish: label to the left
            label_x = mx - 3.5
            label_y = my + 0.7
            anchor = "end"

        parts.append(f'''    <text x="{label_x}" y="{label_y}"
          font-family="Helvetica, Arial, sans-serif" font-size="2.2"
          fill="{color}" text-anchor="{anchor}" font-style="italic">{label}</text>''')

    return '\n'.join(parts)
```

### Right-Angle Connector (smooth)

```python
def right_angle_arrow_smooth(x1, y1, x2, y2, direction="right-down",
                              marker="arrow-std", color="#333333", width=0.5,
                              radius=3):
    """
    L-shaped connector with rounded corner (radius in mm).
    Much more aesthetic than sharp corners.
    """
    if direction == "right-down":
        # Go right, then curve down
        cx = x2  # corner x
        cy = y1  # corner y
        path = (f"M {x1} {y1} "
                f"L {cx - radius} {cy} "
                f"Q {cx} {cy} {cx} {cy + radius} "
                f"L {x2} {y2}")
    elif direction == "down-right":
        cx = x1
        cy = y2
        path = (f"M {x1} {y1} "
                f"L {cx} {cy - radius} "
                f"Q {cx} {cy} {cx + radius} {cy} "
                f"L {x2} {y2}")
    else:
        path = f"M {x1} {y1} L {x2} {y2}"

    return f'''    <path d="{path}"
          fill="none" stroke="{color}" stroke-width="{width}"
          marker-end="url(#{marker})"/>
'''
```

---

## Specialized Scientific Components

### Battery Cell Cross-Section

```python
def battery_cell_schematic(x, y, w, h):
    """
    Simplified Li-ion battery cell cross-section.
    Layers: current collector | anode | separator | cathode | current collector
    """
    layer_w = w / 7  # 7 layers
    layers = [
        ("Cu CC", "#D4A574", 1),
        ("Anode", "#444444", 1.5),
        ("SEI", "#E8E8E8", 0.3),
        ("Separator", "#F5F5F5", 0.5),
        ("Cathode", "#0072B2", 1.5),
        ("CEI", "#E8E8E8", 0.3),
        ("Al CC", "#C0C0C0", 1),
    ]

    total_weight = sum(l[2] for l in layers)
    parts = [f'    <g id="battery-cell">']

    cx = x
    for name, color, weight in layers:
        lw = w * weight / total_weight
        parts.append(f'''      <rect x="{cx:.1f}" y="{y}" width="{lw:.1f}" height="{h}"
            fill="{color}" stroke="#333333" stroke-width="0.15"/>
      <text x="{cx + lw/2:.1f}" y="{y + h + 2.5}"
            font-family="Helvetica, Arial, sans-serif" font-size="1.5"
            fill="#333333" text-anchor="middle"
            transform="rotate(-45, {cx + lw/2:.1f}, {y + h + 2.5})">{name}</text>''')
        cx += lw

    parts.append('    </g>')
    return '\n'.join(parts)
```

### Equivalent Circuit Element

```python
def resistor_symbol(x, y, w=6, h=2, label="R₁"):
    """Zigzag resistor symbol."""
    points = []
    n_teeth = 4
    dx = w / (n_teeth * 2)
    for i in range(n_teeth * 2 + 1):
        px = x + i * dx
        py = y + (h/2 if i % 2 == 1 else -h/2 if i % 2 == 0 and i > 0 and i < n_teeth*2 else 0)
        if i == 0 or i == n_teeth * 2:
            py = y
        points.append(f"{px:.1f},{py:.1f}")

    return f'''    <g id="resistor-{label}">
      <polyline points="{' '.join(points)}"
                fill="none" stroke="#333333" stroke-width="0.35"/>
      <text x="{x + w/2}" y="{y - h/2 - 1.5}"
            font-family="Helvetica, Arial, sans-serif" font-size="2"
            fill="#333333" text-anchor="middle">{label}</text>
    </g>
'''
```

### Capacitor Symbol

```python
def capacitor_symbol(x, y, gap=1.2, plate_h=4, label="C"):
    """Parallel plate capacitor symbol."""
    return f'''    <g id="capacitor-{label}">
      <line x1="{x}" y1="{y - plate_h/2}" x2="{x}" y2="{y + plate_h/2}"
            stroke="#333333" stroke-width="0.4"/>
      <line x1="{x + gap}" y1="{y - plate_h/2}" x2="{x + gap}" y2="{y + plate_h/2}"
            stroke="#333333" stroke-width="0.4"/>
      <text x="{x + gap/2}" y="{y - plate_h/2 - 1.5}"
            font-family="Helvetica, Arial, sans-serif" font-size="2"
            fill="#333333" text-anchor="middle">{label}</text>
    </g>
'''
```

### Scale Bar

```python
def scale_bar(x, y, length_mm, label="10 μm"):
    """Horizontal scale bar with label."""
    return f'''    <g id="scale-bar">
      <line x1="{x}" y1="{y}" x2="{x + length_mm}" y2="{y}"
            stroke="#000000" stroke-width="0.6"/>
      <line x1="{x}" y1="{y - 1}" x2="{x}" y2="{y + 1}"
            stroke="#000000" stroke-width="0.4"/>
      <line x1="{x + length_mm}" y1="{y - 1}" x2="{x + length_mm}" y2="{y + 1}"
            stroke="#000000" stroke-width="0.4"/>
      <text x="{x + length_mm/2}" y="{y + 3}"
            font-family="Helvetica, Arial, sans-serif" font-size="2"
            fill="#000000" text-anchor="middle">{label}</text>
    </g>
'''
```

### Color Legend / Key

```python
def color_legend(x, y, items, box_size=3, spacing=5):
    """
    items: [("Label", "#color"), ...]
    """
    parts = [f'    <g id="legend">']
    for i, (label, color) in enumerate(items):
        iy = y + i * spacing
        parts.append(f'''      <rect x="{x}" y="{iy}" width="{box_size}" height="{box_size}"
            fill="{color}" stroke="#333333" stroke-width="0.2"/>
      <text x="{x + box_size + 1.5}" y="{iy + box_size * 0.75}"
            font-family="Helvetica, Arial, sans-serif" font-size="2"
            fill="#333333">{label}</text>''')
    parts.append('    </g>')
    return '\n'.join(parts)
```

---

## Layout Algorithms

**Key constraint**: Wherever panels are connected by arrows, the gap between them MUST be ≥ 18mm. The layout functions below enforce this.

### Horizontal Flow Layout

```python
def layout_horizontal(panels, total_width=183, margin=6, arrow_gap=20, no_arrow_gap=6):
    """
    Arrange panels in a single row, left to right.
    arrow_gap: gap between panels that have arrows (minimum 18mm, recommend 20mm)
    no_arrow_gap: gap between panels without arrows

    panels: [{'label': 'a', 'width_ratio': 0.33, 'height': 50,
              'type': 'schematic'|'placeholder', 'arrow_to_next': True|False}, ...]
    """
    # Calculate total gap space
    total_gap = sum(arrow_gap if p.get('arrow_to_next') else no_arrow_gap
                    for p in panels[:-1])
    usable_width = total_width - 2 * margin - total_gap

    x = margin
    positions = []
    for i, p in enumerate(panels):
        pw = usable_width * p['width_ratio']
        positions.append({**p, 'x': x, 'y': margin + 5, 'w': pw, 'h': p['height']})
        if i < len(panels) - 1:
            gap = arrow_gap if p.get('arrow_to_next') else no_arrow_gap
            x += pw + gap

    total_height = margin + 5 + max(p['height'] for p in panels) + margin + 5
    return positions, total_width, total_height
```

### Grid Layout

```python
def layout_grid(panels, total_width=183, margin=6, gap_x=20, gap_y=12, cols=2):
    """
    Arrange panels in a grid with specified column count.
    gap_x=20mm to allow arrows between columns.
    gap_y=12mm for vertical breathing room (use 20mm if vertical arrows).
    """
    import math
    rows = math.ceil(len(panels) / cols)
    usable_w = total_width - 2 * margin - (cols - 1) * gap_x
    cell_w = usable_w / cols

    positions = []
    row_height = panels[0]['height'] if panels else 50
    for i, p in enumerate(panels):
        col = i % cols
        row = i // cols
        px = margin + col * (cell_w + gap_x)
        py = margin + 5 + row * (row_height + gap_y)
        positions.append({**p, 'x': px, 'y': py, 'w': cell_w, 'h': row_height})

    last_row = (len(panels) - 1) // cols
    total_height = margin + 5 + (last_row + 1) * (row_height + gap_y) - gap_y + margin + 5
    return positions, total_width, total_height
```

### Hierarchical Layout (Hero + Supporting)

```python
def layout_hierarchical(panels, total_width=183, margin=6, gap=20,
                        hero_ratio=0.50):
    """
    First panel gets hero_ratio of width, remaining panels stack vertically
    on the right side. gap=20mm for arrows from hero to side panels.
    """
    usable_w = total_width - 2 * margin - gap
    hero_w = usable_w * hero_ratio
    side_w = usable_w * (1 - hero_ratio)
    n_side = len(panels) - 1
    hero_h = panels[0]['height']

    positions = [{**panels[0], 'x': margin, 'y': margin + 5,
                  'w': hero_w, 'h': hero_h}]

    if n_side > 0:
        side_gap = 6
        side_cell_h = (hero_h - (n_side - 1) * side_gap) / n_side
        for i, p in enumerate(panels[1:]):
            py = margin + 5 + i * (side_cell_h + side_gap)
            positions.append({**p, 'x': margin + hero_w + gap, 'y': py,
                              'w': side_w, 'h': side_cell_h})

    total_height = margin + 5 + hero_h + margin + 5
    return positions, total_width, total_height
```

### Top-Bottom Layout (Schematics above, Data below)

```python
def layout_top_bottom(panels_top, panels_bottom, total_width=183,
                      margin=6, gap_x=6, gap_y=20):
    """
    Two rows: schematic panels on top, data placeholders on bottom.
    gap_y=20mm between rows for vertical flow arrows.
    """
    n_top = len(panels_top)
    n_bottom = len(panels_bottom)

    usable_w = total_width - 2 * margin
    top_gap = (n_top - 1) * gap_x if n_top > 1 else 0
    bot_gap = (n_bottom - 1) * gap_x if n_bottom > 1 else 0

    top_cell_w = (usable_w - top_gap) / n_top if n_top > 0 else usable_w
    bot_cell_w = (usable_w - bot_gap) / n_bottom if n_bottom > 0 else usable_w

    top_h = panels_top[0]['height'] if panels_top else 40
    bot_h = panels_bottom[0]['height'] if panels_bottom else 45

    positions = []
    for i, p in enumerate(panels_top):
        px = margin + i * (top_cell_w + gap_x)
        positions.append({**p, 'x': px, 'y': margin + 5, 'w': top_cell_w, 'h': top_h})

    for i, p in enumerate(panels_bottom):
        px = margin + i * (bot_cell_w + gap_x)
        positions.append({**p, 'x': px, 'y': margin + 5 + top_h + gap_y,
                          'w': bot_cell_w, 'h': bot_h})

    total_height = margin + 5 + top_h + gap_y + bot_h + margin + 5
    return positions, total_width, total_height
```

---

## Complete Generation Pipeline

```python
def generate_academic_figure(story, variant='A', journal='nature'):
    """
    Full pipeline: story analysis → layout → SVG generation.

    story = {
        'panels': [
            {'label': 'a', 'type': 'schematic', 'description': 'Battery cell structure',
             'width_ratio': 0.4, 'height': 55, 'content_func': battery_cell_schematic},
            {'label': 'b', 'type': 'placeholder', 'description': 'EIS Nyquist Plot',
             'width_ratio': 0.3, 'height': 55},
            {'label': 'c', 'type': 'placeholder', 'description': 'Cycling Data',
             'width_ratio': 0.3, 'height': 55},
        ],
        'arrows': [
            {'from_panel': 'a', 'to_panel': 'b', 'label': 'Characterize'},
        ],
        'title': None,
    }
    """
    # 1. Get journal dimensions
    widths = {'nature': 183, 'cell': 174, 'acs': 177.8, 'ieee': 181.9}
    total_width = widths.get(journal, 183)

    # 2. Apply layout algorithm
    if variant == 'A':
        positions, w, h = layout_horizontal(story['panels'], total_width)
    elif variant == 'B':
        positions, w, h = layout_grid(story['panels'], total_width)
    else:
        positions, w, h = layout_hierarchical(story['panels'], total_width)

    # 3. Generate SVG
    svg = []
    svg.append(svg_header(w, h))
    svg.append(svg_defs())

    # Render each panel
    for p in positions:
        svg.append(panel_container(p['label'], p['label'],
                                   p['x'], p['y'], p['w'], p['h']))
        if p['type'] == 'placeholder':
            svg.append(plot_placeholder(p['x'], p['y'], p['w'], p['h'],
                                        p['description']))
        elif p['type'] == 'schematic' and 'content_func' in p:
            svg.append(p['content_func'](p['x'] + 2, p['y'] + 2,
                                          p['w'] - 4, p['h'] - 4))

    # Render arrows between panels
    for arrow in story.get('arrows', []):
        # Find panel positions and connect them
        from_p = next(p for p in positions if p['label'] == arrow['from_panel'])
        to_p = next(p for p in positions if p['label'] == arrow['to_panel'])
        x1 = from_p['x'] + from_p['w']
        y1 = from_p['y'] + from_p['h'] / 2
        x2 = to_p['x']
        y2 = to_p['y'] + to_p['h'] / 2
        svg.append(flow_arrow(x1 + 1, y1, x2 - 1, y2, arrow.get('label')))

    svg.append(svg_footer())
    return '\n'.join(svg)
```

---

## Tips for High-Quality Output

1. **Always test coordinates**: Print viewBox dimensions and verify panel positions don't overlap
2. **Use consistent rounding**: Round ALL coordinates to 1 decimal place via `R()` helper — never write raw float arithmetic into SVG attributes
3. **Layer ordering matters**: Backgrounds first, then content, then labels, then arrows on top
4. **Inkscape compatibility**: Include `inkscape:groupmode="layer"` and `inkscape:label` attributes on `<g>` elements for proper layer recognition
5. **Text baseline alignment**: SVG text `y` coordinate is the baseline, not the top — offset accordingly (add ~70% of font-size to center vertically)
6. **Test in Inkscape**: The generated SVG should open cleanly with all layers visible in the Layers panel
7. **Label-boundary separation**: Always verify that `label_y < content_area_y` (labels above, not on, the boundary)
8. **Containment check**: Every text element should be within its parent rect/group bounds

---

## Custom Color Scheme Support

When users provide custom colors, use this system to build a complete style dictionary.

### Style Dictionary Structure

```python
def make_style(accent1, accent2=None, accent3=None, accent4=None,
               bg='#FFFFFF', dark_mode=False):
    """
    Build a complete style dict from user-provided accent colors.
    Automatically derives text, arrow, placeholder, and fill colors.
    """
    import colorsys

    def hex_to_rgb(h):
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16)/255 for i in (0, 2, 4))

    def rgb_to_hex(r, g, b):
        return f'#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}'

    def lighten(hex_color, amount=0.85):
        r, g, b = hex_to_rgb(hex_color)
        return rgb_to_hex(r + (1-r)*amount, g + (1-g)*amount, b + (1-b)*amount)

    def darken(hex_color, amount=0.6):
        r, g, b = hex_to_rgb(hex_color)
        return rgb_to_hex(r*amount, g*amount, b*amount)

    def shift_hue(hex_color, degrees=30):
        r, g, b = hex_to_rgb(hex_color)
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        h = (h + degrees/360) % 1.0
        r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
        return rgb_to_hex(r2, g2, b2)

    # Auto-derive missing accents
    if accent2 is None:
        accent2 = shift_hue(accent1, 30)
    if accent3 is None:
        accent3 = shift_hue(accent1, 60)
    if accent4 is None:
        accent4 = shift_hue(accent1, 90)

    # Derive dependent colors
    text_color = '#F0F0F0' if dark_mode else '#222222'
    muted_text = '#888888' if not dark_mode else '#AAAAAA'
    arrow_color = darken(accent1, 0.7)

    return {
        'bg': bg,
        'panel_bg': 'none',  # or lighten(accent1, 0.95) for warm style
        'panel_border': 'none',
        'box_fill': '#FFFFFF' if not dark_mode else '#2A2A2A',
        'box_fill_1': lighten(accent1, 0.88),
        'box_fill_2': lighten(accent2, 0.88),
        'box_fill_3': lighten(accent3, 0.88),
        'box_stroke': accent1,
        'box_stroke_w': 0.4,
        'box_rx': 2,
        'accent1': accent1,
        'accent2': accent2,
        'accent3': accent3,
        'accent4': accent4,
        'arrow_color': arrow_color,
        'arrow_width': 0.55,
        'text_color': text_color,
        'muted_text': muted_text,
        'ph_fill': lighten(accent1, 0.95),
        'ph_stroke': lighten(accent1, 0.5),
        'label_size': 3.5,
    }
```

### Colorblind Safety Check

```python
def check_colorblind_safety(colors):
    """
    Basic check: ensure all color pairs have sufficient luminance contrast.
    Returns list of warnings.
    """
    def relative_luminance(hex_c):
        r, g, b = (int(hex_c.lstrip('#')[i:i+2], 16)/255 for i in (0,2,4))
        def linearize(c):
            return c/12.92 if c <= 0.04045 else ((c+0.055)/1.055)**2.4
        return 0.2126*linearize(r) + 0.7152*linearize(g) + 0.0722*linearize(b)

    warnings = []
    for i, c1 in enumerate(colors):
        for j, c2 in enumerate(colors):
            if j <= i:
                continue
            l1, l2 = relative_luminance(c1), relative_luminance(c2)
            ratio = (max(l1,l2) + 0.05) / (min(l1,l2) + 0.05)
            if ratio < 2.0:
                warnings.append(
                    f"Low contrast between {c1} and {c2} (ratio {ratio:.1f}). "
                    f"These may be hard to distinguish for colorblind readers."
                )
    return warnings
```

### Preset Palettes

```python
PRESET_PALETTES = {
    'wong': {  # Nature Methods colorblind-safe (default)
        'accent1': '#0072B2', 'accent2': '#E69F00',
        'accent3': '#009E73', 'accent4': '#D55E00',
    },
    'acs': {
        'accent1': '#2166AC', 'accent2': '#B2182B',
        'accent3': '#1B7837', 'accent4': '#762A83',
    },
    'ieee': {
        'accent1': '#0000CC', 'accent2': '#CC0000',
        'accent3': '#008800', 'accent4': '#333333',
    },
    'nature_blues': {
        'accent1': '#08519C', 'accent2': '#3182BD',
        'accent3': '#6BAED6', 'accent4': '#9ECAE1',
    },
    'monochrome': {
        'accent1': '#333333', 'accent2': '#666666',
        'accent3': '#999999', 'accent4': '#BBBBBB',
    },
}
```
