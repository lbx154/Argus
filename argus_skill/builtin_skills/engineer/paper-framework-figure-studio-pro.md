---
name: Paper Framework Figure Studio Pro
description: "Argus-adapted autonomous S0-S7 figure workflow from paper-framework-figure-studio-pro-v3.1.4a. The engineer agent executes each stage in sequence — reading the paper, extracting module facts, exploring layout directions, refining candidates with project-specific content, co-designing figure+caption+legend, and jointly auditing for paper fidelity. Use when a paper needs a non-data conceptual figure. Prompt template marker: argus-image2-paper-prompt-v1."
category: paper-figures
version: "3.2.0-argus"
scientist_model: gpt-5.5
created_at: "2026-05-28"
source: "paper-framework-figure-studio-pro-v3.1.4a"
---

# Paper Framework Figure Studio Pro

Argus-native adaptation of `paper-framework-figure-studio-pro-v3.1.4a`.
The original skill uses strict human-in-the-loop step alternation; this
version replaces the human with the engineer agent, who executes S0-S7
autonomously in sequence. The agent reads the paper artifacts, extracts
facts, makes layout decisions, generates candidates, and audits the
result — doing everything a human collaborator would do.

Do not hand-write image prompts from scratch. Do not skip stages. Do not
use generic placeholder labels. The agent must read and understand the
actual paper before drawing anything.

Source: `paper-framework-figure-studio-pro-v3.1.4a`
([github.com/c-narcissus/paper-framework-figure-studio-pro](https://github.com/c-narcissus/paper-framework-figure-studio-pro))

## When to use

- The paper needs a non-data conceptual figure: Figure 1, method overview,
  architecture diagram, pipeline figure, agent workflow schematic.
- Data/metric/result plots are NOT handled here; use matplotlib scripts.

## S0-S7 Workflow — Agent Executes Each Stage

The agent executes each stage below in order. Each stage must be completed
before moving to the next. This is the same S0-S7 workflow as the original
skill, with the agent acting as the human operator.

### S0-PAPER-FOUNDATION — Read the paper and extract facts

The agent reads the project's research artifacts and method code to build
the factual foundation. This is NOT optional — it is the basis for
everything that follows.

Read these files:
- `research/NARRATIVE_REPORT.md`, `research/RESEARCH_BRIEF.md`
- `paper/RESULTS_REPORT.md`, `research/EXPERIMENT_PLAN.md`
- Method source code under `code/`
- `paper/main.tex` if it exists (for existing method description)

Extract and record (in working memory or a scratch file):
- **Module inventory**: every named module, component, model, or stage
- **Data/control flow**: what connects to what, arrows and their direction
- **Core contribution**: which module is the paper's main novelty
- **Core mechanism substeps**: the internal steps of the core module (these
  are `non_droppable_core_steps` — they CANNOT be hidden in an empty box)
- **Input sources / output targets**: what goes in and comes out
- **Baselines**: what the method is compared against
- **Benchmarks / evidence anchors**: evaluation datasets, key metrics
- **What must NOT appear**: no Argus internals, GPU IDs, API routes, etc.

### S1-FIGURE-STRATEGY — Decide figure type and reader path

Based on S0 extraction, decide:
- Figure type: method overview / architecture / pipeline / agent workflow
- Reader path: where should the eye go first? What is the story?
- Layout grammar: horizontal flow, nested containers, hub-spoke, etc.
- Information density: what goes in the figure vs caption vs legend
- Core mechanism visibility plan: how to show the main contribution
  (nested cards, inset, zoom panel — never an empty box)

### S2-SKETCH-EXPLORE — Generate diverse exploration candidates

Generate exactly 3 image-2 rasters with structurally DIFFERENT layout variants.
Do NOT generate more than 3 exploration candidates — it wastes tokens and time.
Pick 3 variants that are maximally different (e.g., one horizontal pipeline,
one nested containers, one hub-and-spoke). The goal is divergence — explore
which spatial structure best communicates the method.
Use the `content` parameter with project-specific labels from S0.

First write all 3 prompt files, then generate all 3 images IN PARALLEL using
the batch command. Do NOT generate images one at a time — use the parallel
batch mode so you can continue working on other tasks while images render:

```bash
# Step 1: Write all prompt files first (fast, no API calls)
python -m argus_skill.tools.image_tool paper-prompt \
  --out paper/figures/method-overview.variant-01.prompt.txt \
  --figure-title "<title>" --content "<content>" \
  --layout-variant "<variant A>" --force

python -m argus_skill.tools.image_tool paper-prompt \
  --out paper/figures/method-overview.variant-02.prompt.txt \
  --figure-title "<title>" --content "<content>" \
  --layout-variant "<variant B>" --force

python -m argus_skill.tools.image_tool paper-prompt \
  --out paper/figures/method-overview.variant-03.prompt.txt \
  --figure-title "<title>" --content "<content>" \
  --layout-variant "<variant C>" --force

# Step 2: Submit image generation to a sub-agent (returns immediately)
python -m argus_skill.tools.subagent submit \
  --task-id fig-explore \
  --description "Generate 3 Figure 1 exploration candidates" \
  --command "python -m argus_skill.tools.image_tool generate \
    --prompt-file paper/figures/method-overview.variant-01.prompt.txt \
    --out paper/figures/method-overview.variant-01.png --size 1536x1024 --force && \
  python -m argus_skill.tools.image_tool generate \
    --prompt-file paper/figures/method-overview.variant-02.prompt.txt \
    --out paper/figures/method-overview.variant-02.png --size 1536x1024 --force && \
  python -m argus_skill.tools.image_tool generate \
    --prompt-file paper/figures/method-overview.variant-03.prompt.txt \
    --out paper/figures/method-overview.variant-03.png --size 1536x1024 --force"

# ... continue with other work while images generate ...

# Check when done
python -m argus_skill.tools.subagent status --task-id fig-explore
```

### S3-DIRECTION-SELECT — Pick the best structural direction

Review the S2 candidates. Select 1-2 directions based on:
- Paper fidelity: does it faithfully represent the method?
- Core mechanism visibility: is the main contribution prominent?
- Reader path: is the story clear at first glance?
- Layout quality: clean, dense, no wasted space

### S4-CANDIDATE-BRIEF — Write candidate contracts with figure-caption co-design

For each selected direction, prepare a formal candidate contract:
- **Title**: short, paper-specific
- **Content block**: all labels spelled exactly as they should appear
- **Caption plan**: what the caption will explain (not drawn in pixels)
- **Legend plan**: arrow types, color meanings, icon semantics
- **Core mechanism visibility**: how the contribution is shown internally
- **Body reference**: how the paper text will refer to this figure

The content block must use actual project module names, NOT generic labels.

**DO** (example):
```
- Title: "PairScorer: Auxiliary Operation-Aware Candidate Ranking"
- Show: "HTML Context" -> "BoW Hash Encoder" -> "Pair Scoring Head" ->
  "Candidate Ranking" + "Auxiliary Op Head (9-class)" -> "Action Prediction"
- Core inset: "Pair Scoring Head" internals — [ctx, cand, |ctx-cand|,
  ctx*cand] concatenation -> MLP -> logit
- Benchmarks: "Mind2Web", "ALFWorld", "TravelPlanner"
```

**DON'T**:
```
- Show: "Source/input" -> "Parse/build step" -> "Quality gate" ->
  "Memory/state" -> "Agent/execution" -> "Output/result"
```

### S5-CANDIDATE-IMAGE — Generate refined formal candidates

Generate exactly 3 refined image-2 rasters based on the S4 contracts.
Do NOT generate more than 3 refined candidates. These should be clean
publication-ready schematic references:
- Straight or gently curved connectors with consistent stroke weight
- Modular cards, panels, callouts, compact mechanism insets
- Restrained color coding and high contrast
- Semantically relevant icons chosen because they express the paper
- Short readable labels, not hand-written style
- The core contribution module shows its internal mechanism

```bash
python -m argus_skill.tools.image_tool paper-prompt \
  --out paper/figures/<id>.prompt.txt \
  --figure-title "<from S4>" --content "<from S4>" \
  --layout-variant "<from S4>" --force

python -m argus_skill.tools.image_tool generate \
  --prompt-file paper/figures/<id>.prompt.txt \
  --out paper/figures/<id>.png --size 1536x1024 --force

python -m argus_skill.tools.image_tool inspect --image paper/figures/<id>.png
python -m argus_skill.tools.image_tool review \
  --image paper/figures/<id>.png \
  --prompt-file paper/figures/<id>.prompt.txt \
  --out paper/figures/<id>.png.review.json
```

### S6-FINAL-SELECT — Choose and finalize the figure-text bundle

Select the best S5 candidate. Produce the complete figure-text bundle:
- **Selected image path** (displayed/recorded)
- **Final title, caption, legend, body-reference text**
- **Paper recheck**: verify module names, arrows, and claims match the paper
- **Manuscript note**: if the figure reorganizes the method for clarity,
  note what writing changes the paper text needs

Copy the selected candidate to the stable final filename, then IMMEDIATELY
run sync-paper-metadata to align all hashes and metadata. Do NOT manually
edit JSON files to fix hashes — sync does it automatically:
```bash
cp paper/figures/<selected>.png paper/figures/method_overview.png
cp paper/figures/<selected>.png.json paper/figures/method_overview.png.json
cp paper/figures/<selected>.png.inspect.json paper/figures/method_overview.png.inspect.json
cp paper/figures/<selected>.png.review.json paper/figures/method_overview.png.review.json
cp paper/figures/<selected>.prompt.txt paper/figures/method_overview.prompt.txt

# MUST run sync immediately after copy — fixes all hash/metadata alignment
"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill.tools.image_tool sync-paper-metadata \
  --project-root . --image paper/figures/method_overview.png \
  --prompt-file paper/figures/method_overview.prompt.txt \
  --figure-id method_overview --figure-type method
```

### S7-FINAL-JOINT-AUDIT — Terminal audit of figure + caption bundle

Review the selected figure + caption + legend + body-reference as ONE unit.
This is a bounded checklist, not an open-ended review:

| Check | What to verify |
|---|---|
| Paper fidelity | Module names match the paper; no invented components |
| Core mechanism | Main contribution is NOT an empty box; internal steps visible |
| Arrow semantics | Data flow directions are correct |
| Color/icon semantics | Colors and icons have consistent meaning |
| Label accuracy | Every label in the figure is spelled correctly |
| Figure-caption split | Image shows structure; caption explains details |
| Reader path | Eye flow matches the method's logic |

Verdict: `PASS`, `TEXT-REPAIR` (fix caption/legend → S6), `IMAGE-REPAIR`
(fix prompt → S4/S5), or `DIRECTION-REPAIR` (rethink layout → S1/S3).

If PASS: sync metadata and validate:
```bash
python -m argus_skill.tools.image_tool sync-paper-metadata \
  --project-root . --image paper/figures/method_overview.png \
  --prompt-file paper/figures/method_overview.prompt.txt \
  --figure-id method_overview --figure-type method

# Self-audit the image-2 figure requirements before handoff;
# the L2 reviewer verifies these artifacts directly against the draft/review stage checklists.
```

## Figure Rules

- A core contribution module cannot be an empty generic box. Show its
  internal mechanism through nested cards, a connected inset, a compact
  loop, or a small mechanism panel.
- The figure carries the reader path and structure; the caption carries
  definitions, caveats, and numeric evidence. Do not stuff explanatory
  text into image pixels.
- Generate 3 attempts per direction, pick the cleanest. Do NOT generate
  more than 3 per round — quality comes from better prompts, not more
  attempts. Common defects: misspelled labels, vertical text, overlapping
  cards. Re-prompt with sharper constraints; do not fix in post.
- If prompt/provenance/manifest hashes drift, run `sync-paper-metadata`
  from the real generated files. Do not patch JSON hashes by hand.
- Do not draw non-data figures with matplotlib, TikZ, SVG, or PIL. Use
  image-2 exclusively. If the result is ugly, improve the prompt and
  regenerate; never hand-draw a replacement.
