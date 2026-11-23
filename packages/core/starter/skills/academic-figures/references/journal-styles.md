# Journal Style Reference

Exact formatting specifications for major academic publishers. Use these dimensions and styles when generating figures.

---

## Dimension Specifications

### Nature / Nature Journals
- **Single column**: 89 mm wide (max)
- **1.5 column**: 120 mm wide
- **Double column / full width**: 183 mm wide (max)
- **Max height**: 247 mm (full page)
- **Resolution**: 300 DPI minimum for raster elements
- **File format**: Vector preferred (EPS, PDF, SVG → convert to EPS/PDF for submission)
- **Font size**: 5–7 pt (minimum readable), 7–8 pt recommended for labels
- **Line weight**: 0.5–1.5 pt

### Cell / Cell Press
- **Single column**: 85 mm wide
- **1.5 column**: 114 mm wide
- **Full width**: 174 mm wide
- **Max height**: 230 mm
- **Font size**: 6–8 pt for labels, panel letters 8–10 pt bold
- **Line weight**: 0.5–1.0 pt preferred

### ACS (JACS, ACS Nano, Chem. Mater., etc.)
- **Single column**: 3.33 in (84.7 mm)
- **Double column**: 7.00 in (177.8 mm)
- **Max height**: 9.19 in (233.4 mm)
- **TOC graphic**: 3.25 × 1.75 in (82.6 × 44.5 mm) — specific size required
- **Font size**: 8 pt minimum (ACS is strict about this)
- **Preferred fonts**: Helvetica, Arial, Times New Roman, Courier
- **Line weight**: 0.5–1.0 pt

### IEEE
- **Single column**: 3.5 in (88.9 mm)
- **Double column**: 7.16 in (181.9 mm)
- **Max height**: 9.5 in (241.3 mm)
- **Font size**: 8 pt minimum
- **Preferred fonts**: Times New Roman, Helvetica, Arial
- **Line weight**: 0.75–1.5 pt (IEEE tends to use slightly thicker lines)

### Elsevier (Joule, Energy & Environmental Science, etc.)
- **Single column**: 90 mm
- **1.5 column**: 140 mm
- **Full width**: 190 mm
- **Font size**: 6–8 pt
- **Line weight**: 0.5–1.0 pt

### Science / AAAS
- **Single column**: 5.7 cm (57 mm)
- **Two-thirds page**: 12.1 cm (121 mm)
- **Full width**: 17.4 cm (174 mm)
- **Font size**: 6–8 pt, Helvetica preferred
- **Line weight**: 0.5–1.0 pt

---

## Default Dimensions (when no journal specified)

Use Nature-style as the safe default:
- **Width**: 183 mm (full width, most versatile)
- **Height**: Auto based on content (typically 80–150 mm for multi-panel)
- **Convert to SVG units**: 1 mm = 3.7795 px (at 96 DPI screen) — but set SVG `width`/`height` in mm with `viewBox` for scalability

### SVG Dimension Setup
```xml
<!-- Full-width Nature figure, 183mm × 120mm -->
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     width="183mm" height="120mm"
     viewBox="0 0 183 120">
  <!-- All coordinates are now in mm -->
</svg>
```

---

## Color Palettes

### Default: Colorblind-Safe Scientific Palette
Based on Wong (2011) Nature Methods palette — widely considered the standard for accessible scientific figures.

| Name | Hex | RGB | Use |
|------|-----|-----|-----|
| Blue | #0072B2 | (0, 114, 178) | Primary data / category 1 |
| Orange | #E69F00 | (230, 159, 0) | Category 2 / highlights |
| Green | #009E73 | (0, 158, 115) | Category 3 / positive |
| Vermillion | #D55E00 | (213, 94, 0) | Category 4 / alert |
| Sky Blue | #56B4E9 | (86, 180, 233) | Category 5 / background accent |
| Yellow | #F0E442 | (240, 228, 66) | Category 6 / highlight (use sparingly) |
| Purple | #CC79A7 | (204, 121, 167) | Category 7 |
| Black | #000000 | (0, 0, 0) | Text, outlines |

### Neutral / Structural Colors
| Name | Hex | Use |
|------|-----|-----|
| Dark Gray | #333333 | Secondary text, borders |
| Medium Gray | #666666 | Annotations, light borders |
| Light Gray | #CCCCCC | Panel dividers, grid lines |
| Very Light Gray | #F0F0F0 | Background fills, placeholder fills |
| White | #FFFFFF | Panel backgrounds |

### Nature-Style Blues (for single-hue sequential data)
`#deebf7` → `#9ecae1` → `#3182bd` → `#08519c`

### ACS-Style (tends toward richer, more saturated colors)
Blue: `#2166AC`, Red: `#B2182B`, Green: `#1B7837`, Purple: `#762A83`, Orange: `#E08214`

### IEEE-Style (often uses primary colors, bold contrasts)
Blue: `#0000FF`, Red: `#FF0000`, Green: `#008000`, Black: `#000000`, Gray: `#808080`

---

## Typography Specifications

### Font Stack (priority order)
```
font-family="Helvetica, Arial, Liberation Sans, sans-serif"
```

### Text Size Hierarchy
| Element | Size | Weight | Example |
|---------|------|--------|---------|
| Panel label | 10pt | Bold | **a** |
| Axis title | 8pt | Regular | Voltage (V) |
| Axis tick label | 7pt | Regular | 0.5, 1.0, 1.5 |
| Annotation | 7pt | Regular | Li⁺ diffusion |
| Caption text | 7pt | Italic | (within figure) |
| Legend text | 7pt | Regular | Sample A |
| Title (if any) | 10pt | Bold | Electrochemical Performance |

### Point-to-mm Conversion
- 1 pt = 0.3528 mm
- 7 pt ≈ 2.47 mm
- 8 pt ≈ 2.82 mm
- 10 pt ≈ 3.53 mm

### SVG Text Sizing
When using mm-based viewBox, set font-size in mm:
```xml
<text font-family="Helvetica, Arial, sans-serif" font-size="2.8" font-weight="bold">a</text>
<!-- 2.8mm ≈ 8pt -->
```

---

## Panel Label Conventions

### Standard (Nature, Cell, Science)
- Lowercase bold: **a**, **b**, **c**, **d**
- Position: Top-left corner of each panel, outside the panel border
- Offset: ~2mm left of panel edge, ~1mm above panel top

### ACS
- Uppercase bold or lowercase bold depending on journal: **(A)**, **(B)** or **a)**, **b)**
- Some ACS journals use parentheses, some don't — default to lowercase bold without parentheses

### IEEE
- Often uses **(a)**, **(b)** with parentheses
- Sometimes standalone text beneath panels

### Formatting in SVG
```xml
<!-- Panel label positioned at top-left of panel -->
<text x="2" y="8" font-family="Helvetica, Arial, sans-serif"
      font-size="3.5" font-weight="bold" fill="#000000">a</text>
```

---

## Arrow and Connector Styles

### Standard Arrow Marker Definition
```xml
<defs>
  <!-- Standard arrow (filled, sharp) -->
  <marker id="arrow-standard" viewBox="0 0 10 10" refX="9" refY="5"
          markerWidth="4" markerHeight="4" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="#333333"/>
  </marker>

  <!-- Thin arrow (open, for annotations) -->
  <marker id="arrow-thin" viewBox="0 0 10 10" refX="9" refY="5"
          markerWidth="3" markerHeight="3" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10" fill="none" stroke="#333333" stroke-width="1.5"/>
  </marker>

  <!-- Block arrow (for workflows) -->
  <marker id="arrow-block" viewBox="0 0 10 10" refX="9" refY="5"
          markerWidth="5" markerHeight="5" orient="auto-start-reverse">
    <path d="M 0 2 L 7 5 L 0 8 z" fill="#0072B2"/>
  </marker>
</defs>
```

### Line Weights
| Use | Stroke Width (mm) | SVG stroke-width |
|-----|--------------------|-----------------|
| Panel border | 0.3–0.5 | 0.35 |
| Data connector / flow arrow | 0.4–0.6 | 0.5 |
| Schematic outline | 0.3–0.5 | 0.4 |
| Thin annotation line | 0.15–0.25 | 0.2 |
| Bold emphasis line | 0.6–1.0 | 0.75 |

---

## Graphical Abstract / TOC Graphic Specifications

### ACS TOC Graphic
- **Exact size**: 3.25 × 1.75 inches (82.55 × 44.45 mm)
- Must visually summarize the paper
- No panel labels (single unified image)
- Text should be minimal and large (10pt+)
- Simple, high-impact, recognizable at thumbnail size

### Cell Graphical Abstract
- **Size**: 16 × 10 cm (160 × 100 mm)
- Color, no text required (but short labels OK)
- Often uses illustrated/iconic style

### Nature / Science
- Graphical abstracts not typically required
- But summary figures at the start of the paper are common
- Full-width format (183mm or 174mm)

---

## Common Mistakes to Avoid

1. **Text too small**: Below 6pt is unreadable in print — check ALL text sizes
2. **Inconsistent fonts**: Mixing serif and sans-serif within data labels
3. **Color overload**: More than 7 distinct colors in one figure
4. **Missing panel labels**: Every panel needs a letter label
5. **Raster in vector**: Embedding low-res PNGs destroys print quality
6. **Non-standard units**: Always use SI units in labels
7. **Excessive decoration**: 3D effects, shadows, gradients on data — avoid
8. **Alignment errors**: Panels not vertically/horizontally aligned on grid
9. **Inconsistent line weights**: Mixing thick and thin lines without reason
10. **Poor white space**: Panels crammed together or drowning in empty space
