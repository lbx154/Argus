---
name: paper-illustration-image2
description: "Generate publication-quality academic illustrations using gpt-image-2 with a multi-stage iterative workflow. Argus planner/reviewer agents specify and audit; the configured codex/image route renders. Use when user says 'generate figure', 'architecture diagram', 'method figure', or needs AI-generated paper illustrations."
category: paper-figures
version: "1.0"
scientist_model: gpt-5.4
created_at: "2025-07-17"
---

# Paper Illustration Image2

Generate publication-quality paper figures using **Argus planner/reviewer agents**
and **gpt-image-2** as the raster renderer via the configured codex/image backend.

For EMNLP/ACL paper projects, data/metric/result plots are the only figures that may be locally scripted. Every other paper-facing figure, including method overviews, architecture/system diagrams, schematics, qualitative/example visuals, teasers, and explanatory diagrams, must be generated through this image-2 workflow and registered in `paper/figures/IMAGE2_FIGURES.json`; never substitute matplotlib, TikZ, SVG/PIL/HTML canvas, screenshots, cleaned PDFs, or manual vector redraws for those non-data figures.

## Core Design Philosophy

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                    MULTI-STAGE ITERATIVE WORKFLOW                        │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   User Request                                                           │
│       │                                                                  │
│       ▼                                                                  │
│   ┌─────────────┐                                                        │
│   │   Planner   │ ◄─── Step 1: Parse request, create initial prompt     │
│   │             │      - Extract components, labels, and data flow       │
│   │             │      - Write a paper-ready figure brief                │
│   └──────┬──────┘                                                        │
│          │                                                               │
│          ▼                                                               │
│   ┌─────────────┐                                                        │
│   │   Layout    │ ◄─── Step 2: Optimize layout description               │
│   │   Review    │      - Refine component positioning                    │
│   │             │      - Optimize spacing and grouping                   │
│   └──────┬──────┘                                                        │
│          │                                                               │
│          ▼                                                               │
│   ┌─────────────┐                                                        │
│   │   Style     │ ◄─── Step 3: CVPR/NeurIPS/EMNLP style verification    │
│   │   Check     │      - Check palette, arrows, and label standards      │
│   │             │      - Tighten the prompt before rendering             │
│   └──────┬──────┘                                                        │
│          │                                                               │
│          ▼                                                               │
│   ┌─────────────┐                                                        │
│   │ gpt-image-2 │ ◄─── Step 4: Image generation                         │
│   │  renderer   │      - Call images.generate API                        │
│   │             │      - Accept only native image output                 │
│   └──────┬──────┘                                                        │
│          │                                                               │
│          ▼                                                               │
│   ┌─────────────┐                                                        │
│   │  Reviewer   │ ◄─── Step 5: STRICT visual review + SCORE (1-10)      │
│   │   STRICT!   │      - Verify logic, labels, arrows, and aesthetics    │
│   │             │      - Reject unclear or non-paper-ready figures       │
│   └──────┬──────┘                                                        │
│          │                                                               │
│          ▼                                                               │
│   Score ≥ 9? ──YES──► Accept & Output                                    │
│          │                                                               │
│          NO                                                              │
│          │                                                               │
│          ▼                                                               │
│   Generate SPECIFIC improvement feedback ──► Loop back to Step 2        │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

## Constants

- **RENDERER = `gpt-image-2`** — via configured API endpoint
- **MAX_ITERATIONS = 5** — Maximum refinement rounds
- **TARGET_SCORE = 9** — Minimum acceptable score (1-10)
- **OUTPUT_DIR = `paper/figures/ai_generated/`** — Output directory
- **TEXT_LANGUAGE = `English`** — Default figure text language
- **SIZE = `1536x1024`** — Landscape for method/architecture figures (NEVER 1024x1024 for paper figs)
- **TIMEOUT = 600** — gpt-image-2 takes 200-300s per call

## Step 1: Parse & Plan

Given a figure request (e.g., "architecture diagram for our multi-agent memory system"):

1. Identify figure type: architecture | method-flow | comparison | teaser | ablation-visual
2. Extract key components, their relationships, data flow direction
3. Determine visual hierarchy (what's most important?)
4. Write a structured figure brief:

```yaml
figure_type: architecture
title: "SHM-Gate Memory Architecture"
components:
  - name: "Input Queue"
    position: left
    visual: rectangle with rounded corners
  - name: "Admission Gate"
    position: center
    visual: diamond decision node
  - name: "Hierarchical Memory"
    position: right
    visual: stacked layers (3 tiers)
flow: left-to-right
color_scheme: blue-gray academic (no saturated colors)
labels: all components labeled, arrows annotated with operations
size: 1536x1024 (landscape)
```

## Step 2: Prompt Construction

Convert the brief into a detailed gpt-image-2 prompt. Rules:

- **Be exhaustive**: describe every element, its position, color, size
- **Specify text**: every label, font size relative to figure, placement
- **Academic style**: muted colors, clean lines, no gradients, no 3D effects
- **White background**: always white or very light gray
- **Arrow style**: thin black arrows with small heads, labeled where needed
- **Font**: sans-serif, consistent size throughout

Template:
```
Create a publication-quality academic figure on a white background.
Style: clean, minimal, suitable for a top-tier ML conference paper (EMNLP/NeurIPS).
No gradients, no 3D effects, no decorative elements.

[Detailed description of all components, positions, connections, labels...]

Color palette: [specific hex codes or descriptions]
Text: all labels in English, sans-serif font, clearly readable at print size.
Layout: [left-to-right | top-to-bottom], with clear visual hierarchy.
```

## Step 3: Style Verification Checklist

Before rendering, verify the prompt against these academic figure standards:

- [ ] White/light background (no dark themes)
- [ ] Muted color palette (blues, grays, soft accents)
- [ ] No gradients or 3D effects
- [ ] All text is English and readable at column-width (3.25 inches)
- [ ] Arrows are thin, black, with small heads
- [ ] Components are clearly separated with adequate whitespace
- [ ] Visual hierarchy matches importance hierarchy
- [ ] Figure tells its story without needing the caption

## Step 4: Render

```python
from openai import OpenAI  # or AzureOpenAI
import base64
from pathlib import Path

# Use credentials from ~/.argus-skill/capabilities/model_api.json
# or environment variables (OPENAI_API_KEY, OPENAI_BASE_URL)
client = ...  # configured client

resp = client.images.generate(
    model="gpt-image-2",
    prompt=OPTIMIZED_PROMPT,
    n=1,
    size="1536x1024"
)
img_bytes = base64.b64decode(resp.data[0].b64_json)
output_path = Path("paper/figures/ai_generated/") / f"{figure_name}.png"
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_bytes(img_bytes)
```

Cache by SHA-256(prompt) to avoid re-spending tokens on identical prompts.

## Step 5: Review & Score

Before review, enforce this ordering:

1. Wait for the `image_tool generate` / `code/generate_image_2.py` command to finish.
2. Confirm the output file exists and is non-empty with `test -s <output.png>` or the project helper return payload.
3. Run `image_tool inspect` and `image_tool review` only after the generated raster exists.

Do not launch inspect/review in parallel with generation. A missing-file inspect/review failure is noise, not a useful candidate verdict; rerun the ordered sequence instead of treating it as image quality feedback.

After generation, review the image against these criteria (score 1-10):

| Criterion | Weight | What to check |
|---|---|---|
| Logic correctness | 30% | Do arrows/flows match the described method? |
| Label clarity | 20% | Are all labels readable and correctly placed? |
| Academic style | 20% | Matches conference figure standards? |
| Visual hierarchy | 15% | Most important elements stand out? |
| Completeness | 15% | All described components present? |

**Score ≥ 9**: Accept. Save final image + manifest entry.
**Score < 9**: Generate specific improvement feedback, loop back to Step 2.

## Output Manifest

Maintain `paper/figures/IMAGE2_FIGURES.json`:

```json
{
  "figures": [
    {
      "name": "architecture_overview",
      "file": "paper/figures/ai_generated/architecture_overview.png",
      "prompt_sha256": "abc123...",
      "score": 9,
      "iterations": 2,
      "size": "1536x1024",
      "generated_at": "2025-07-17T10:30:00Z"
    }
  ]
}
```

## Integration with Paper Pipeline

- Called by `emnlp-paper-drafting` when figures are needed
- Output referenced in LaTeX as `\includegraphics[width=\columnwidth]{figures/ai_generated/...}`
- Manifest consumed by `emnlp-format-preflight` to verify all figures exist

## Pitfalls

- Each gpt-image-2 call takes 200-300s — be patient, set timeout=600
- Concurrency 4-8 with ThreadPoolExecutor is safe for batch generation
- Only concurrentize independent complete generations; do not race inspect/review before each output file exists.
- Expect occasional 429s — back off honoring Retry-After headers
- Never use 1024x1024 for paper figures; use image-route-safe dimensions divisible by 16, for example landscape 1536x1024 or 1920x1088, or portrait 1024x1536.
- Text in images can be imperfect; for critical labels, regenerate with a tighter prompt instead of locally overlaying labels on a non-data figure.
