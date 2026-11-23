---
name: academic-figures
description: "Generate editable SVG scientific schematics, graphical abstracts and multi-panel experimental workflow diagrams for Nature, Science, Cell, IEEE and ACS journals."
---

# Academic Figure Generator

Generate publication-ready, editable SVG figures for top-tier academic journals. Outputs are directly editable in **Inkscape** and **Ipe** (via SVG import).

## Core Workflow

```
User provides research story/idea
        ↓
Analyze → identify panels, flow, hierarchy
        ↓
Determine figure type (multi-panel figure OR graphical abstract/TOC)
        ↓
Design 2-3 variants (different layouts AND visual styles)
        ↓
Generate SVG files with:
  - Clean vector graphics (shapes, arrows, labels)
  - Labeled placeholder regions for data plots
  - Journal-compliant typography and dimensions
        ↓
Present all variants for user to choose/refine
```

---

## Step 0: Read References

**BEFORE generating any SVG**, read the reference files:

1. **Read** `references/journal-styles.md` — journal dimension/formatting rules
2. **Read** `references/svg-guide.md` — SVG generation patterns, component library

These contain critical information about exact dimensions, fonts, color palettes, arrow spacing rules, and reusable SVG component code. Do NOT skip this step.

---

## Step 1: Analyze the User's Story

When the user describes their figure, extract:

1. **Narrative arc**: What story does the figure tell? (e.g., "We synthesized X, characterized it with Y, and it performs Z")
2. **Panel inventory**: How many distinct panels? What does each show?
   - **Schematic panels**: Diagrams, workflows, device structures, molecular illustrations
   - **Data panels**: Plots, spectra, images (these become labeled placeholders)
   - **Annotation panels**: Scale bars, legends, color bars
3. **Reading order**: Left-to-right? Top-to-bottom? Circular workflow?
4. **Target journal** (if specified): Determines dimensions, fonts, column widths
5. **Figure type**: Determine from context:
   - **Multi-panel research figure** — the default. Panels labeled a, b, c... with schematics and data placeholders.
   - **Graphical abstract** — single unified visual summarizing the paper, no panel labels, more iconic/illustrated style.
   - **TOC graphic** — very small (ACS: 82.55 × 44.45 mm), high-impact thumbnail, minimal text, bold visual.

If the user hasn't specified a journal, default to Nature-style formatting (widely compatible).

### Asking Clarifying Questions

If the user's description is ambiguous, ask ONE focused question. Do not overwhelm with multiple questions. Prefer making reasonable assumptions and noting them.

---

## Custom Color Schemes

Users can supply their own color scheme, which overrides the default style palette. Accept colors in any format (hex, RGB, color names) and normalize to hex for SVG output.

### How Users Specify Colors

Users may say things like:
- "Use blue (#2166AC) and red (#B2182B) as the main colors"
- "Match my lab's brand colors: navy, gold, and white"
- "Use the ACS style colors"
- "I want a monochrome figure in shades of gray"
- "Use the same palette as my matplotlib viridis plots"

### Color Scheme Structure

When the user provides custom colors, map them into these roles:

```python
custom_scheme = {
    'accent1': '#...',    # Primary color (boxes, main arrows, key elements)
    'accent2': '#...',    # Secondary color (secondary elements, highlights)
    'accent3': '#...',    # Tertiary color (supporting elements)
    'accent4': '#...',    # Quaternary (optional, for 4+ category figures)
    'bg': '#FFFFFF',      # Figure background (default white unless specified)
    'text_color': '#...',  # Auto-derived: black for light bgs, white for dark
    'arrow_color': '#...', # Auto-derived: darkened version of accent1
}
```

### Auto-Derivation Rules

If the user provides only 1-2 colors, derive the rest:

- **1 color given**: Use it as accent1. Generate accent2 as a 30° hue-shifted variant, accent3 as a 60° variant. Keep backgrounds white.
- **2 colors given**: Use as accent1 and accent2. Derive accent3 by interpolating or shifting hue.
- **3+ colors given**: Map directly to accent1, accent2, accent3, accent4.
- **Background**: Default white unless the user specifically requests a colored or dark background.
- **Text color**: Use #222222 (near-black) for light backgrounds, #F0F0F0 for dark backgrounds.
- **Arrow color**: Use a darkened (70% luminance) version of accent1, or #444444 if accent1 is too light.
- **Placeholder fill**: Use a very light tint (10% opacity) of accent1.

### Applying Custom Colors to Variants

When custom colors are provided, ALL variants use the same color scheme (since the user wants these specific colors). The variants still differ in layout and structural style (line weights, corner radii, spacing), but use the same palette.

However, if the user says "give me different color options too", then vary the color application across variants:
- Variant A: Colors used minimally (outlines only)
- Variant B: Colors used for fills on key elements
- Variant C: Colors used boldly (filled backgrounds, colored arrows)

### Colorblind Safety Check

When the user provides custom colors, check whether the palette is distinguishable for common color vision deficiencies (protanopia, deuteranopia). If the chosen colors are problematic (e.g., red + green without luminance difference), warn the user:

```
"Note: The red (#FF0000) and green (#00AA00) you chose may be hard to 
distinguish for readers with red-green color vision deficiency. Consider 
using blue (#0072B2) instead of green, or adding pattern fills to 
differentiate elements."
```

Do not refuse to use the colors — just flag the concern once and proceed.

---

## Step 2: Design Variants

Generate **2-3 variants** that differ in BOTH layout AND visual style.

### Variant Differentiation Strategy

Variants should give the user genuinely different options — not just the same figure rearranged. Differentiate on **two axes**:

**Axis 1: Layout**
| Layout | Description | Best For |
|--------|-------------|----------|
| Horizontal flow | Wide, single-row or 2-row, left-to-right reading | Narrative/sequential stories |
| Grid/matrix | Balanced rows × cols | Comparison studies, multi-condition |
| Hierarchical | Large hero panel + stacked supporting panels | One key result + supporting data |
| Top-bottom | Schematic row above, data row below | Setup → results stories |

**Axis 2: Visual Style**
| Style | Description |
|-------|-------------|
| Clean/minimal | White backgrounds, thin lines, maximum white space, no fills on boxes |
| Color-accented | White backgrounds with colored fills on key elements, colored section headers |
| Muted/warm | Light warm-gray backgrounds (#F8F6F3), subtle colored accents, softer look |
| High-contrast | Bold colors from the Wong palette, strong borders, prominent arrows |

### Example Variant Combinations
```
Variant A: Horizontal flow + Clean minimal style
Variant B: Grid layout + Color-accented style
Variant C: Hierarchical + Muted/warm style
```

Each variant must:
- Use the **same content** (same panels, same labels)
- Differ meaningfully in spatial arrangement AND visual treatment
- Include **panel labels** (a, b, c, d...) in bold lowercase
- Include **placeholder boxes** for every data plot with descriptive text
- Follow the target journal's dimension constraints

### Graphical Abstract / TOC Variant Strategy

For graphical abstracts and TOC graphics, differentiate variants by:
- **Composition**: Left-to-right flow vs. circular/radial vs. layered/stacked
- **Visual weight**: Icon-heavy vs. text-balanced vs. single-hero-image
- **Style**: Flat/geometric vs. illustrated/organic vs. diagram/technical

---

## Step 3: Generate SVG Files

### SVG Generation Method

Use **Python with raw SVG XML** generation. Do NOT use matplotlib for the figure layout itself (matplotlib is for plots that go INTO placeholders, not for the figure skeleton).

```python
# Generate SVG using string templating or xml.etree.ElementTree
# See references/svg-guide.md for the full component library
```

### Critical SVG Requirements

1. **Editability First**
   - Every element must be in its own SVG group (`<g>`) with a descriptive `id`
   - Use named layers: `id="panel-a"`, `id="panel-b"`, `id="arrows"`, `id="labels"`
   - All text as `<text>` elements (NOT paths) so it remains editable
   - No embedded raster images — pure vector only
   - **CRITICAL**: All `id` attributes must be single-line, no newlines. Use sanitized slugs: `id="placeholder-eis-nyquist"` not descriptions with special characters.
   - **CRITICAL**: All coordinates must be rounded to 1 decimal place. No floating-point noise like `43.666666666666664`.

2. **⚠️ Text-Boundary Overlap Prevention (CRITICAL)**

   This is the #1 source of ugly figures. SVG text `y` is the BASELINE, not the top — so text at y=10 with a rect at y=10 means the text sits ON the border.

   **Panel label placement**:
   - Labels go 2.5mm ABOVE the panel content area top edge
   - Reserve 7mm of headroom above the first row of panels for labels
   - Formula: `label_y = content_area_y - 2.5`
   - Content area starts at `panel_y + 1.0` (1mm padding below the panel top)

   **Dimension annotations in placeholders**:
   - Place INSIDE the placeholder rect, 2mm from the bottom-right corner
   - Formula: `dim_y = rect_y + rect_h - 2`, `dim_x = rect_x + rect_w - 2`
   - NEVER place dimension text outside the placeholder rect bounds

   **General text containment rule**:
   - Every text element must be fully inside its parent container (group, rect, or allocated area)
   - Check: `text_y > container_y + 1` AND `text_y < container_y + container_h - 1`
   - Schematic labels (e.g., layer names below a cell diagram): position at `cell_bottom + 3.5mm`, ensure this stays within the panel area

2. **Typography**
   - Primary: `Helvetica, Arial, sans-serif` (universally available, journal-standard)
   - Panel labels: **Bold, 8-10pt** (lowercase a, b, c...)
   - Axis/annotation text: Regular, 7-8pt
   - Title text (if any): Bold, 9-10pt
   - ALL text must have `font-family`, `font-size`, `font-weight` explicitly set

3. **Color Palettes** (see `references/journal-styles.md` for full details)
   - Default to colorblind-safe palette
   - Use muted, professional tones — no garish saturated colors
   - Consistent use of color across all panels (same meaning = same color)

4. **Plot Placeholders**
   - Dashed-border rectangles with light gray fill (#F5F5F5)
   - Centered descriptive text: e.g., "EIS Nyquist Plot", "CV Curves", "XRD Pattern"
   - Small instruction text below: "Replace with data plot (recommended: 300 DPI PNG or vector)"
   - Clearly dimensioned so the user knows the exact size to export their plot

5. **⚠️ Arrows and Connectors — SPACING RULES (CRITICAL)**

   Arrows are a major source of visual clutter when done poorly. Follow these rules strictly:

   **Minimum arrow length**: 10mm. NEVER generate arrows shorter than 10mm. If the gap between panels is less than 12mm, DO NOT place an arrow — instead increase the gap or use a different layout.

   **Arrow clearance zones**: Arrows must have at least 3mm clearance from any text, panel border, or other element on all sides.

   **Label placement for arrows**:
   - Place labels ABOVE horizontal arrows (offset y by -3.5mm from arrow midpoint)
   - Place labels to the LEFT of vertical arrows (offset x by -4mm from arrow midpoint)
   - NEVER place labels directly on top of the arrow line
   - **Curved arrow labels**: When a curved arrow exits from the bottom of a box, the default "label above midpoint" places text ON the box bottom border. In this case, place the label BELOW the curve midpoint instead (+3.5mm offset).
   - If there isn't room for a label, omit it — the arrow alone is clear enough

   **Icons/symbols near layer boundaries**:
   - In layered schematics (e.g., battery cross-sections), small icons or labels (like Li⁺ ions) must not sit on layer boundaries. Keep icons ≥5% of the total width away from any inter-layer edge.

   **Arrow-to-panel spacing**: Arrows should start 3mm after the source panel edge and end 3mm before the target panel edge. This means the inter-panel gap must be at least 16mm for an arrow with labels (10mm arrow + 3mm start clearance + 3mm end clearance).

   **Practical consequence**: When designing layouts, set inter-panel gaps to **18-22mm** wherever an arrow is needed. This is non-negotiable.

   **Arrow position when passing between adjacent boxes**:
   - When an arrow connects two boxes at the same vertical level, do NOT place the arrow at the box vertical center — the label above will collide with the box top edge.
   - Instead, position the arrow at the **lower third** of the box height (65% down from top). This gives the label 3+ mm clearance above the box border.
   - Formula: `arrow_y = box_y + box_h * 0.65` (not `box_y + box_h / 2`)

   - Clean arrowheads defined as SVG markers
   - Consistent stroke width (0.5-0.75pt for connectors)
   - Use curved (quadratic Bézier) connectors when the flow changes direction — never sharp right-angle corners

6. **Grouping and Layers** (critical for Inkscape/Ipe editing)
   ```xml
   <g id="panel-a" inkscape:groupmode="layer" inkscape:label="Panel A">
     <g id="panel-a-background">...</g>
     <g id="panel-a-content">...</g>
     <g id="panel-a-labels">...</g>
   </g>
   ```

### File Naming Convention

```
figure_variant_A.svg
figure_variant_B.svg
figure_variant_C.svg
```

---

## Graphical Abstract & TOC Graphic Guidelines

Graphical abstracts and TOC graphics are fundamentally different from multi-panel figures:

### Key Differences from Multi-Panel Figures
- **No panel labels** (no a, b, c)
- **Single unified composition** — not divided into discrete panels
- **More iconic/illustrated** — simplified representations, bolder strokes
- **Minimal text** — only essential labels, large enough to read at thumbnail size (10pt+)
- **Must tell the story at a glance** — even more compressed than the 3-second rule

### Design Approach
1. Identify the **one key transformation** the paper describes (Input → Method → Output)
2. Represent it as a **visual flow** with 2-4 iconic elements
3. Use **bold colors** (fewer than 4) and **clean shapes**
4. Add a plot placeholder only if the key result IS a plot (e.g., a performance curve)
5. Include the paper's **key metric** as large text if applicable (e.g., "98.5% efficiency")

### ACS TOC Graphic Specifics
- **EXACT size**: 82.55 × 44.45 mm — this is mandatory, not approximate
- Very small canvas → every mm counts
- Minimum text size 8pt (ACS rule)
- Must work as a ~2cm thumbnail in the journal table of contents

### Graphical Abstract Template Structure
```
[Input/Problem] → [Method/Innovation] → [Output/Result]
     Icon            Diagram/Arrow          Icon + Key number
```

---

## Step 4: Present to User

After generating all variants:

1. **Save all SVG files** to `/mnt/user-data/outputs/`
2. **Present files** using `present_files` tool
3. Provide a **brief comparison** — one line per variant:
   ```
   Variant A: Horizontal flow, clean minimal style. Best for sequential narratives.
   Variant B: 2×2 grid, color-accented. Best for comparison studies.
   Variant C: Hero + side panels, muted warm tones. Best when one result dominates.
   ```
4. Offer to **refine** the chosen variant (move panels, resize, adjust colors, add elements)

---

## Design Principles for Journal Figures

### The 3-Second Rule
A reader scanning the journal should grasp the figure's main message within 3 seconds. This means:
- Clear visual hierarchy (most important panel is largest/most prominent)
- Minimal clutter — every element must earn its place
- Consistent flow direction (don't make the eye jump around)

### White Space is Content
- Generous margins between panels (minimum 10mm, 18-22mm where arrows connect panels)
- Don't cram content — a figure that's 80% full reads better than one at 100%
- Use alignment grids to keep panels consistent

### Schematic Design Language
When drawing scientific schematics (not data plots), follow these conventions:
- **Devices/cells**: Clean cross-sections with labeled layers
- **Molecules/particles**: Simplified geometric forms, not photorealistic
- **Workflows**: Numbered steps with directional arrows
- **Comparisons**: Side-by-side with matched scales and aligned baselines
- **Processes**: Input → Process → Output with clear flow

### Common Panel Types (with SVG strategies)

| Panel Type | SVG Approach |
|-----------|-------------|
| Battery cell schematic | Layered rectangles with gradient fills for electrodes |
| Circuit diagram | Lines, components as grouped symbols |
| Workflow/pipeline | Rounded rectangles connected by arrows |
| Molecular structure | Circles (atoms) + lines (bonds), simplified |
| Experimental setup | Simplified equipment icons as grouped paths |
| Data plot placeholder | Dashed rectangle with descriptive label |
| Comparison table | Grid of cells with text/icons |
| Scale bar / legend | Grouped reference elements |

---

## Refinement Loop

After the user selects a variant, support iterative refinement:
- "Move panel (c) below panel (b)"
- "Make the arrows thicker"
- "Change the color scheme to ACS style"
- "Add a scale bar to panel (a)"
- "Make it fit a single column (89mm wide)"

For each refinement, regenerate ONLY the selected variant with changes applied.

---

## Quality Checklist (verify before presenting)

- [ ] All panel labels present (a, b, c...) in consistent style (skip for graphical abstracts)
- [ ] **Panel labels are 2.5mm ABOVE content area** (never ON a boundary line)
- [ ] **All text is INSIDE its parent container** (no text overlapping borders)
- [ ] **Dimension text is INSIDE placeholder rects** (2mm padding from bottom-right)
- [ ] All text is editable `<text>` elements (not converted to paths)
- [ ] All `id` attributes are single-line slugs (no newlines, no special characters)
- [ ] All coordinates are rounded to 1 decimal place (no floating-point noise)
- [ ] SVG elements are grouped into named layers
- [ ] Plot placeholders are clearly marked with descriptive text
- [ ] Dimensions match target journal specifications
- [ ] Color palette is colorblind-safe (or user was warned about issues)
- [ ] Custom colors (if provided) are applied consistently across all elements
- [ ] **ALL arrows are ≥10mm long** with ≥3mm clearance from text/borders
- [ ] **No arrow labels overlap** with arrows, panels, or other text
- [ ] **Curved arrow labels** placed BELOW when exiting a box bottom (not above into the box border)
- [ ] **Icons/symbols in schematics** are ≥5% width from any layer boundary
- [ ] Inter-panel gaps are ≥18mm wherever arrows are present
- [ ] White space is adequate between panels (≥7mm headroom above first row)
- [ ] Font sizes are within journal requirements (typically 6-10pt)
- [ ] File is well-formed SVG that opens in Inkscape without errors
- [ ] SVG validates as valid XML (no malformed tags or attributes)
