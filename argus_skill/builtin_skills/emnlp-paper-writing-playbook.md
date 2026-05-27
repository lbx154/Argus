---
name: emnlp-paper-writing-playbook
description: "End-to-end operational playbook for producing an EMNLP/ACL/NeurIPS paper from a research idea. Covers project skeleton, LLM client, benchmark design, baselines, LaTeX styling, gpt-image-2 figures, page budget, citation hygiene, experiment audit, and submission preflight. References argus-skill builtins without hard-coded host paths."
category: paper-writing
version: "2.0"
scientist_model: gpt-5.4
created_at: "2025-07-17"
updated_at: "2025-07-27"
---

# EMNLP Paper Writing Playbook (Argus workflow)

> **For:** any LLM agent that needs to take a research idea or repo and produce
> a publication-ready EMNLP / ACL / NeurIPS-style PDF — paper text, benchmark
> code, baselines, experiments, figures, tables, all of it.
>
> **Author of this playbook:** distilled from a 30-turn paper-writing session
> that produced two complete papers (`SHM-Gate`, `SkillCycle`) end-to-end
> from a research plan, including LaTeX, Figma-style figures, statistical
> tests, ablations, and SOTA-claim framing.

---

## 0. Prerequisites & where to look first

Before doing anything else, consult the **argus-skill built-in skills** and
**reference repositories** on this server.

| Goal | Where to look |
|---|---|
| Auto-research pipeline orchestration | argus-skill builtin: `auto-research-pipeline.md` |
| Benchmark runner & experiment execution | argus-skill builtin: `agent-research-benchmark-runner.md` |
| EMNLP paper drafting contract | argus-skill builtin: `emnlp-paper-drafting.md` |
| Submission assurance gate | argus-skill builtin: `research-submission-assurance-gate.md` |
| Claims-evidence audit | argus-skill builtin: `claims-evidence-audit.md` |
| Paper exemplar learning | argus-skill builtin: `paper-exemplar-pdf-learning.md` |
| Research brief → experiment plan | argus-skill builtin: `research-brief-to-experiment-plan.md` |
| Results analysis & figures | argus-skill builtin: `research-results-analysis-and-figures.md` |
| Paper review/revision loop | argus-skill builtin: `paper-review-revision-loop.md` |
| Academic language review | argus-skill builtin: `emnlp-academic-language-review.md` |
| Format preflight | argus-skill builtin: `emnlp-format-preflight.md` |
| Full paper-writing playbook (this file) | argus-skill builtin: `emnlp-paper-writing-playbook.md` |
| ML paper writing skill (extended) | `/root/AI-Research-SKILLs/20-ml-paper-writing/ml-paper-writing/SKILL.md` |
| Academic plotting skill | `/root/AI-Research-SKILLs/20-ml-paper-writing/academic-plotting/SKILL.md` |
| Image-2 figure generation | argus-skill builtin: `paper-illustration-image2.md` |
| Research pipeline orchestration | argus-skill builtin: `auto-research-pipeline.md` |
| Experiment audit | argus-skill builtin: `experiment-audit.md` |
| Citation audit | argus-skill builtin/domain skill: `domains/research-ops/citation-audit.md` |
| Result-to-claim conversion | argus-skill builtin: `result-to-claim.md` |
| 90-skill AI research library | `/root/AI-Research-SKILLs/` (see CLAUDE.md for index) |

**Builtin skill locations:**
- Per-project export: `<project>/argus_builtin_skills/*.md` and `<project>/argus_builtin_skills/**/*.md`
- Package resource: `argus_skill.builtin_skills`
- Source checkout, when the launcher supplies one: `$ARGUS_SKILL_SOURCE_ROOT/argus_skill/builtin_skills/`

**Hard rule:** never invent BibTeX entries from memory. AI hallucination rate
on citations is ≈40 %. Use Semantic Scholar / arXiv / CrossRef tools to
fetch real BibTeX, or mark with `% UNVERIFIED` and tell the user.

---

## 1. The end-to-end workflow (what to actually do)

```
  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
  │  Research plan  │ →  │  Code skeleton  │ →  │  Run experiments│
  │  / draft idea   │    │  + benchmark    │    │  → results JSON │
  └─────────────────┘    └─────────────────┘    └─────────────────┘
                                                          │
  ┌─────────────────┐    ┌─────────────────┐    ┌────────▼────────┐
  │  Compile PDF,   │ ←  │  Write LaTeX    │ ←  │  Tables+figures │
  │  iterate fast   │    │  body sections  │    │  from results   │
  └─────────────────┘    └─────────────────┘    └─────────────────┘
```

**Order matters.** Don't write the abstract first; write it last after the
numbers exist. Don't pick a method name until you've seen at least the
smoke-test ablation. Don't add a citation until you've verified the paper
exists.

---

## 2. Project skeleton (copy this exactly)

```
my-paper/
├── main.tex                  # the paper
├── refs.bib                  # only verified entries
├── acl.sty                   # copy from EMNLP/ACL template
├── acl_natbib.bst
├── appendix_algo.tex         # algorithm pseudocode
├── appendix_bench.tex        # benchmark construction details
├── appendix_extra.tex        # secondary figures + tables
├── appendix_repro.tex        # hyperparams, seeds, compute, prompts hash
├── bench/                    # generated benchmarks (jsonl)
├── code/
│   ├── llm.py                # Azure / OpenAI client wrapper with caching
│   ├── build_bench.py        # deterministic benchmark generator
│   ├── methods.py            # baselines + your method
│   ├── run.py                # main eval harness
│   ├── make_paper.py         # tables.tex + .pdf figures from results
│   ├── make_pub_figs.py      # publication-styled matplotlib figures
│   └── gen_figs.py           # GenAI Figma-style diagrams
├── figs/                     # final PDFs/PNGs
├── results/                  # per-method JSON outputs
└── tables/                   # auto-generated .tex tables
```

**Template files to copy**: take `acl.sty`, `acl_natbib.bst`, and the
preamble of an existing `main.tex` from one of the worked-example papers.
Don't rebuild them.

---

## 3. The LLM client wrapper (`code/llm.py`)

You need a deterministic, cached LLM client. The project template ships
one at `code/llm.py`. Use the model API vault at
`~/.argus-skill/capabilities/model_api.json` for credentials.

Key pattern:

```python
"""Cached LLM client. All training-free; one model knob."""
from __future__ import annotations
import os, time, json, hashlib
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent / ".llm_cache"
CACHE_DIR.mkdir(exist_ok=True)

def chat(messages, model="gpt-5-mini", temperature=0.0,
         max_tokens=512, use_cache=True):
    """Cached chat call. Cache key includes the entire prompt, so
    deterministic decoding makes the cache an exact-replay store."""
    key = hashlib.sha256(
        json.dumps([model, temperature, max_tokens, messages],
                    sort_keys=True).encode()
    ).hexdigest()
    cache_f = CACHE_DIR / f"{key}.json"
    if use_cache and cache_f.exists():
        d = json.loads(cache_f.read_text())
        return d["content"], d.get("usage", {})
    # Call your configured API (OpenAI, Azure, local proxy)
    # Save to cache_f on success
    ...
```

**Why caching:** deterministic re-runs are cheap and reviewable. Once you
have a result, the cache lets you iterate on the analysis without paying
the API again. **Always** include the entire `messages` list in the cache
key — system prompt changes invalidate cache.

**API configuration:** load credentials from the model API vault
(`~/.argus-skill/capabilities/model_api.json`) or from environment
variables (`OPENAI_API_KEY`, `OPENAI_BASE_URL`). The codex CLI
config at `~/.codex/config.toml` defines provider endpoints.

---

## 4. Benchmark design rules

### Real benchmarks over synthetic

For a submission-quality paper, **use existing real benchmarks or official
task/data releases for the main evidence**. Synthetic/local tasks are useful
only as smoke tests, unit tests, prompt-format diagnostics, or ablations that
are clearly labeled as such. They must not become headline tables, final
claims, or submission-readiness evidence.

Final EMNLP evidence needs:
- at least 3 independent executed real benchmark source families, not planned
  diagnostic rows or same-family slices;
- raw scored rows for every required method/baseline condition on the selected
  multi-source matrix;
- raw scored rows under `experiments/**`, not only benchmark manifests;
- a named evaluated model/backend. Internal manifests may record local compute
  details, but the manuscript should report only paper-facing facts: evaluated
  model/backend class, framework/runtime or benchmark harness, checkpoint or
  scorer identifier, training/inference settings, budget/decoding, seeds, and
  high-level compute/cost when relevant. If no local GPU exists, run the
  approved hosted route, with `gpt-5-mini` as the default low-cost backbone,
  and record temperature, top_p, max_tokens, budget, cache/retry/timeout policy,
  and stopping rules in manifests. Do not put local device ordinals, CUDA
  variables, cache paths, workstation names, private endpoints, or Argus/Codex
  route configuration in rendered paper prose.

### Construction rules

1. **3+ independent real benchmark families** is the minimum for final evidence.
   Each source should stress a different capability or failure mode; variants
   from one suite count as one family.
2. **5 task families** inside a source mix can be useful for analysis. Each family stresses a different
   skill (filtering, planning, calculation, routing, recovery).
3. **Within a family, vary surface entities** (names, dates, numbers) but
   keep the latent procedure constant. This separates pattern-matching
   from skill-abstraction.
4. **Each family has a "failure trap"**: a tempting wrong branch the model
   will hit unless it learns the right rule. The trap is what lets skills
   demonstrate value.
5. **3 difficulties (easy / mid / hard)** vary noise count or step length,
   not the skill itself.
6. **Deterministic scoring when the benchmark provides gold**, but the evaluated
   agent may call `gpt-5-mini` or another approved model; record the model and
   decoding settings.
7. **Hand-verify >= 20 random episodes** before running anything.

Skeleton:

```python
def make_episode(family, i, difficulty):
    rng = random.Random(f"2026_{family}_{i}")
    # ... family-specific construction ...
    return {
        "id": f"{family}_{difficulty}_{i:03d}",
        "family": family,
        "difficulty": difficulty,
        "prompt": "...",                    # what the agent sees
        "oracle_skill": "name_of_skill",    # gold rule (for analysis)
        "gold_answer": gold,                # deterministic gold
        "gold_steps": [...],                # for trace analysis
        "failure_trap": "...",              # for verifier prompts
        "allowed_actions": [...],
    }
```

### Secondary: real-environment subset

Add a small (≤30) real-env subset (e.g. ALFWorld) **after** synthetic works.
Adapt to your schema; don't use original Docker. Score with relaxed metrics
(step-recall, full-solve fraction). Scaffold the prompt with a one-shot
example to avoid format confounds.

---

## 5. Method + baselines (`code/methods.py`, `code/run.py`)

### Mandatory baselines

| Baseline | What it is | Why |
|---|---|---|
| `no_skill` | Plain prompt, no memory | floor |
| `raw_memory` | Cache last-K solved (prompt, answer) pairs | strong baseline that's hard to beat |
| `reflexion` | Verbal reflection on failures | Shinn 2023 |
| `static_skill_lib` | Distil skill from every episode, no verifier | Voyager 2024 negative |
| `your_method` | Your contribution | … |

Each method has the same signature:

```python
def method_X(ep: dict, model: str, state: dict) -> dict:
    """Returns: {answer, n_tokens, skills_used, trace}"""
```

`state` is a per-run accumulator the harness mutates. **Crucial**: methods
that don't accumulate across episodes will be invisible to ablations.

### The harness (`run.py`)

```python
for i, ep in enumerate(samples):
    out = method_fn(ep, model, state)
    success = score_episode(out["answer"], ep["gold_answer"])
    records.append({...})
    post_episode_update(method_name, ep, out, success, state, model,
                         future_eps=samples[i+1:i+6])  # for held-out replay
```

**Sequential by design** — methods that learn need to see episodes in order.
Don't parallelize within a method. Do parallelize across methods (separate
processes).

### Verifier patterns

Two verifier styles. Both have their place:

1. **LLM-as-judge** (cheap): show the candidate skill + K held-out tasks,
   ask "would this skill solve each?". Weak signal but free.
2. **Executable verifier** (strong, frontier): actually *run* the skill
   on K held-out tasks and score against deterministic gold. Inspired by
   Process Reward Models (Lightman 2023). This is the SOTA pattern.

Always cap K to 3. Admit threshold: ≥0.5 (LLM-judge) or ≥0.66 (exec).

---

## 6. LaTeX preamble + design tokens

This is the styling kit that makes tables / figures look consistent across
the paper. Drop into the preamble of `main.tex`:

```latex
\documentclass[11pt]{article}
\usepackage[review]{acl}     % or [final] for camera-ready
\usepackage{times}
\usepackage{latexsym}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{microtype}
\usepackage{inconsolata}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{amsmath, amssymb}
\usepackage{xcolor}
\usepackage{colortbl}
\usepackage{multirow, makecell, enumitem}
\usepackage{algorithm, algpseudocode}
\usepackage{xspace}

% --- Table styling tokens (use everywhere) ---
\definecolor{tabheader}{HTML}{F3F4F6}     % light gray header
\definecolor{tabours}{HTML}{FFE2D1}       % peach for "ours" row
\definecolor{tabrow}{HTML}{FAFAFA}        % alternating row tint
\definecolor{tabaccent}{HTML}{E76F51}     % coral, for degradation
\newcommand{\headrow}[1]{\textbf{#1}}
\newcommand{\oursrow}{\rowcolor{tabours}}

% --- Method/benchmark macros ---
\newcommand{\method}{\textsc{YourMethod}\xspace}
\newcommand{\bench}{\textsc{YourBench}\xspace}
```

### Table template

Every table follows this pattern (auto-generate from results JSON):

```latex
\begin{table}[t]\centering\footnotesize
\setlength{\tabcolsep}{4pt}
\renewcommand{\arraystretch}{1.15}
\resizebox{\columnwidth}{!}{%
\begin{tabular}{lccc}
\toprule
\rowcolor{tabheader}
\headrow{Method} & \headrow{Acc.\,$\uparrow$} & \headrow{Lib} & \headrow{Tok/ep} \\
\midrule
Baseline 1 & 0.900 & 0 & 247 \\
\rowcolor{tabrow} Baseline 2 & 0.717 & 63 & 429 \\
Baseline 3 & \textcolor{tabaccent}{0.463} & 218 & 561 \\
\midrule
\oursrow \textbf{\method} (ours) & \textbf{0.958} & \textbf{19} & 264 \\
\bottomrule
\end{tabular}}
\caption{Specific, numerical, narrative. ``Our method wins by X at Y cost.''}
\label{tab:main}
\end{table}
```

Key choices:
- `\footnotesize` + `\tabcolsep=3-4pt` + `\arraystretch=1.15` + `\resizebox`
  → never overflow column.
- Header in **light gray** (NOT dark blue) — readable, neutral.
- "Ours" row in **soft peach**.
- Alternating rows in `tabrow` for long tables.
- `tabaccent` (coral) for highlighting degraded values.
- **Bold the winning value** in each column.

---

## 7. Figures — two paths, one style

Per `academic-plotting/SKILL.md`:

| Figure type | Tool | Output |
|---|---|---|
| Architecture / system diagram | **GenAI** (gpt-image-1 / Gemini 3) | PNG |
| Workflow / before-after / qualitative cards | **GenAI** | PNG |
| Bar charts, line plots, heatmaps, scatter | **matplotlib** | PDF |

### GenAI prompt template (Figma-style)

The 6-section prompt that produces consistent, paper-grade diagrams:

```
1. FRAMING (2 lines)
   "Create one polished EMNLP method figure variant.
    [STYLE_NAME] technical diagram for an NLP paper."

2. STYLE BLOCK (15 lines, copy verbatim — most important section)
   "VISUAL STYLE — MODERN MINIMAL:
    - Clean block-based Figma style with rounded cards (10px radius)
    - Soft pastel fills, dark-gray 2px borders
    - Background off-white #FBFAF7, stroke dark #1F2933
    - Pastel palette: peach #FFE2D1, blue #DCECFF, mint #D4E6D4,
      lavender #EADFFF, amber #F8E9C4, gold #E9C46A
    - Comic Sans MS-like rounded handwritten font, tidy
    - Moderate badge use: simple recognizable icons (database,
      gear, brain, magnifying glass, check, X)
    - No heavy shadows, no gradients, no photorealism, no Excalidraw
    - Large readable labels, short phrases"

3. VARIANT LAYOUT (2-3 lines, swap per figure)
   "Variant: side-by-side comparison with vertical dashed divider;
    LEFT 'Baseline (status quo)' coral; RIGHT 'Ours' emerald."

4. CONTENT (50-100 lines, EXACT labels)
   Spell every chip / box / arrow EXACTLY. Models misspell otherwise.

5. NEGATIVE CONSTRAINTS
   "Avoid: code snippets, tiny text, large empty areas, photorealism,
    gradients, decorative blobs, stock icons."

6. ASPECT RATIO
   "1536x1024 landscape" or "1024x1536 portrait"
```

**Generate 6--20 layout variants per non-data figure** at quality="high" by
changing only the layout block. Pick the cleanest reviewed image-2 raster.
Common defects: misspelled labels, character-level vertical text,
overlapping cards. Re-prompt with sharper constraints; do not fix in post.

### gpt-image-2 reference implementation

Use **`gpt-image-2`** for architecture/method figures. The project template
ships a helper at `code/generate_image_2.py` and `code/generate_image2_figure.py`.
For the full multi-stage iterative workflow with visual review, see the
argus-skill builtin `paper-illustration-image2.md`.

The argus-skill image tool (`argus_skill.tools.image_tool`) wraps the
configured endpoint from `~/.argus-skill/capabilities/model_api.json`.

Minimal usage pattern:

```python
from openai import AzureOpenAI  # or OpenAI
import base64
from pathlib import Path

# Load credentials from model_api.json or environment
MODEL = "gpt-image-2"
SIZE = "1536x1024"  # landscape for method figures (NEVER 1024x1024)
TIMEOUT = 600.0     # gpt-image-2 takes 200-300s per call

# client = ... (configured from your vault/env)
resp = client.images.generate(model=MODEL, prompt=PROMPT, n=1, size=SIZE)
img_bytes = base64.b64decode(resp.data[0].b64_json)
Path("fig.png").write_bytes(img_bytes)
```

**Pitfalls**:
- Use `client.images.generate(...)` for text-to-image; use
  `client.images.edit(image=...)` if you have a reference image to edit.
- Each call takes 200-300s; bump client `timeout=600`.
- Concurrency 4-8 with a `ThreadPoolExecutor` is safe; expect occasional
  429s — back off honoring `Retry-After` from the response headers.
- Cache by SHA-256(prompt) to disk so repeated runs don't re-spend tokens.
- Require `paper/figures/IMAGE2_FIGURES.json` manifest with prompt SHA-256,
  output path, sidecar, inspect, review, and generation provenance.

### gpt-image-2 prompt style — NeurIPS/CS pipeline figures

For full-width method/architecture figures the strongest known recipe is
the 6-section prompt below (used to generate the
\textsc{VideoWorldSkills} pipeline figures). Copy verbatim and only swap
the variant-specific layout.

#### Base style block (paste verbatim)

```text
General style:
- NeurIPS / CS paper method figure, full-width two-column landscape.
- Clean block-based Figma style with rounded cards, neat alignment,
  soft pastel fills, dark gray 2px borders.
- Compact, information-rich, suitable for a PDF page-width figure.
- Comic Sans MS-like rounded handwritten font, but tidy and readable.
- Moderate logo/badge use: a few simple recognizable icons,
  not a logo wall.
- No heavy shadows, no gradients, no photorealism, no messy
  Excalidraw look.
- Large readable labels, short phrases, balanced hierarchy.
```

Chinese style intent (helps when prompting bilingually):

```text
干净、密实、模块化、Figma 风，圆角卡片为主，低饱和浅色块，
少量 badge/logo，少留白但不拥挤。整体适合 NeurIPS/ICLR/CS
论文主图，不要像随手白板，也不要像艺术插画。
```

#### Negative-prompt checklist

```text
Avoid:
- concrete code snippets
- tiny unreadable text
- excessive logos or brand marks
- large empty areas
- photorealistic scenes
- heavy gradients, shadows, glassmorphism, or texture
- messy whiteboard / Excalidraw-heavy sketch style
- arbitrary decorative blobs
- inconsistent terminology between figure and text
```

#### Pinned-content block

State EVERY label that must appear in the figure verbatim. The model
misspells anything you don't quote. Example (for VideoWorldSkills):

```text
Pipeline content that must remain consistent:
- Title: "VideoWorldSkills Pipeline"
- Show: Sources -> Parse & Distill -> Quality Gates -> LM Wiki ->
  Skill-Grounded Agent -> Domain Adapter -> Rendered Outputs ->
  Benchmark Protocol.
- Sources: Video Tutorials, Code Repos, Articles, Static Artifacts.
- LM Wiki contents: Text, Visual, Recipe, Metadata; metadata chips
  include tier, category, tags, source, exec_ok.
- Domains: PPT, Web, Excel, Blender, Reaper, UE5, CAD.
- Outputs: Slides, Site, Sheet, 3D Scene, Audio, Game, CAD Plan.
```

#### Twenty layout-variant prompts (use to seed a batch)

| ID | Name | Variant-specific layout |
|---:|---|---|
| 01 | central wiki hero | Central hero composition: huge LM Wiki card in the middle, source factory on the left, agent/domain/output board on the right, benchmark strip at bottom. Staggered stacked cards behind the wiki. |
| 02 | horizontal swimlanes | Three clean horizontal swimlanes: Build Wiki, Ground the Agent, Benchmark. Use overlapping lane headers and offset cards so it is not too rigid. |
| 03 | sankey funnel | Sankey/funnel: multiple sources merge into distillation, narrow through quality gates, expand into the LM Wiki, then branch into seven domains and outputs. |
| 04 | exploded wiki entry | Exploded-view: the LM Wiki entry is pulled apart into Text, Visual, Recipe, Metadata plates with callout arrows. Acquisition and agent blocks wrap around it. |
| 05 | layered architecture stack | Layered architecture: bottom layer sources, middle layer wiki memory, top layer agent/domain execution. Use shelf-like overlapping horizontal slabs. |
| 06 | pipeline plus gallery | Classic pipeline plus gallery: main pipeline across the top, large output gallery on the right, compact benchmark chart cards along the bottom. |
| 07 | modular dashboard | Dashboard: cards arranged like a dense product analytics dashboard, with LM Wiki as the largest widget and domain badges as a side panel. |
| 08 | radial hub spoke | Hub-and-spoke: LM Wiki as center hub; sources feed from left arc; agent and domains radiate to right; benchmark sits as a bottom control panel. |
| 09 | zigzag pipeline | Z-shaped reading path: acquisition cards across top-left to center, agent cards down and right, output/benchmark along bottom. Numbered step badges. |
| 10 | research poster dense | Dense research-poster style: section headers, compact cards, mini charts, and domain output thumbnails; still clean Figma and paper-friendly. |
| 11 | minimal grayscale accent | Mostly grayscale academic style with two pastel accent colors: blue for wiki, green for execution. Fewer icons, stronger typography, crisp arrows. |
| 12 | color coded phases | Color-coded phases: peach acquisition, blue wiki, green agent, lavender domains, yellow benchmark. Slight overlapping cards and numbered phase tabs. |
| 13 | card deck metaphor | Card-deck metaphor: sources, skills, and outputs appear as tidy fanned decks. The wiki is a large deck of skill cards with one card expanded. |
| 14 | computation graph | Computation graph: nodes and grouped modules, with thin arrows and grouped rounded containers; visually resembles ML system diagrams in conference papers. |
| 15 | dataflow with sidebars | Dataflow with sidebars: main flow through center, left sidebar for sources, right sidebar for domains and outputs, bottom sidebar for benchmark arms. |
| 16 | timeline plus insets | Timeline plus insets: a clean left-to-right timeline with two large inset zoom boxes: one for LM Wiki internals and one for Domain Adapter outputs. |
| 17 | nested containers | Nested containers: big containers for Offline Library Construction and Online Agent Execution; nested subcards inside, with benchmark embedded as a footer. |
| 18 | multi panel abcd | Four-panel academic layout labeled A, B, C, D: A sources/distill, B LM Wiki, C agent/domain execution, D benchmark. Panels overlap slightly and share arrows. |
| 19 | system blueprint | Blueprint-style but light, not dark: pale blue grid background, modular boxes, thin connector routes, neat badges, strong central wiki box. |
| 20 | figma wireframe polished | Polished Figma wireframe: clean component frames, auto-layout-like spacing, visible section tabs, domain/output chips, carefully staggered components. |

Use a fixed style block + pinned-content block, swap only the
variant-specific layout. Generate 6-20 variants per figure; pick the
two cleanest and let the rest go to the appendix or get redrawn in
Figma.

#### Figma redraw tokens (for camera-ready cleanup)

```text
Canvas: 16:9, 1536 x 1024 or 1920 x 1080
Background: #fbfaf7
Stroke: #1f2933, 2 px
Corner radius: 10-16 px
Card padding: 12-20 px
Card gap: 12-24 px
Pastels:
  acquisition: #ffe2d1
  parsing:     #fff2bd
  wiki:        #dcecff
  agent:       #e2f7df
  domains:     #eadfff
  benchmark:   #fff1c9
Text:
  title:          Comic Sans MS / Comic Neue, 38-52 px
  section header: 22-30 px
  card label:     16-22 px
  chips:          12-16 px
```

### matplotlib publication style

```python
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold",
    "axes.labelsize": 9, "xtick.labelsize": 8, "ytick.labelsize": 8,
    "legend.fontsize": 8, "legend.frameon": False,
    "figure.dpi": 200, "savefig.dpi": 300, "savefig.bbox": "tight",
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.alpha": 0.18, "grid.linestyle": "-",
    "grid.linewidth": 0.5, "lines.linewidth": 1.6, "axes.linewidth": 0.7,
})

# Palette (matches table tokens)
OUR_COLOR   = "#E76F51"   # coral — your method
ABL_COLOR   = "#F4A261"   # warm orange — your ablations
BASELINE    = "#7B8794"   # cool grey — baselines (recede)
ACCENT_TEAL = "#2A9D8F"   # secondary highlights
ACCENT_GOLD = "#E9C46A"

# Sizes (ACL/EMNLP)
FIG_SC, FIG_FULL = (3.25, 2.4), (6.7, 2.6)
```

### Figure-placement rules (the part everyone gets wrong)

- ❌ Don't `\begin{figure}[t]` for every figure → all crash to top of page,
  leaving big text gaps and overflow.
- ✅ Use `[t]` for ≤2 critical body figures per page; everything else goes
  to appendix.
- ✅ Use `figure*[t]` (full-width) only for the *one* most-important figure
  (teaser, waterfall, multi-panel scale curves).
- ✅ **Move qualitative / secondary figures to appendix** even if you love
  them. Body has ≤5 figures total. Non-data appendix figures still need real
  image-2 output; only data/metric/result plots may be locally scripted.
- ✅ Body data figures should sit on the Pareto frontier of the story:
  one heatmap, one scatter/Pareto, one trend line, one bar comparison.
  No more.

---

## 8. Page budget (EMNLP long paper)

```
Page 1   Title, authors, 170--220 word abstract, teaser figure
Page 2   Introduction (at least 900 words/full first page, with at least three citations and contributions list)
Page 3   Related Work, start of Method
Page 4   Method (at least 700 words), architecture figure
Page 5   Experimental Setup (at least 550 words), main table
Page 6   Results: ablation table, per-condition table, key figure
Page 7   Analysis: significance, qualitative, discussion
Page 8   Conclusion + Limitations + Ethics
─────────  (8-page body limit; references and appendix begin after page 8)
Page 9+  References + Appendix
```

**Rule**: Conclusion (Sec ≤9) MUST appear by end of page 8. Limitations
and Ethics are body end matter after Conclusion and must fit within the
eight-page body; they do not repair an underfilled pre-Conclusion draft. Do not
force this with `\clearpage`, `\newpage`, `\pagebreak`, or `\FloatBarrier`
immediately before Conclusion; a forced pre-Conclusion break can leave page 8
mostly blank and push the heading to page 9 after minor float changes.

If the body overflows, in priority order: (a) move secondary figures to appendix;
(b) move low-value diagnostics to appendix; (c) tighten repeated score
restatements; (d) merge Limitations bullets. Do not solve overlength by cutting
the Introduction below 900 words, deleting model/benchmark configuration, or
removing the explanation a reviewer needs to understand the work.

---

## 9. The narrative principle (Neel Nanda)

Every paper must have **one** sentence-long contribution:

> "We propose X. We show that X improves Y by Z because W."

If you can't fill in (X, Y, Z, W) in one breath, the paper isn't ready.

The body is structured to defend that one sentence:

- **Intro** sets up Y (the problem) and why W (the mechanism) is missing
  from prior work.
- **Method** specifies X (your contribution) precisely enough that someone
  could re-implement.
- **Results** provides Z (the measurable improvement) with significance
  and ablations.
- **Analysis** explains why W produced Z, with traces / qualitative
  examples.
- **Conclusion** restates X-Y-Z-W in fresh language.

### SOTA framing (when raw accuracy isn't your win)

If your method ties or just barely beats the strongest baseline on raw
accuracy, **define a new metric** that you win on. Examples:

- *Audited Accuracy* (AAcc): best accuracy achievable under a library-size
  budget. Lets a method with smaller library win at small budgets.
- *Memory Consistency Score* (MCS): cleanliness of shared memory.
- *Pareto frontier on (cost, accuracy)*: any non-dominated point counts.

The metric must be motivated by deployment (not invented to make you win).
Auditability, interpretability, latency, memory budget — all defensible.

---

## 10. Citation hygiene (most important rule)

**Never write BibTeX from memory.** Use one of:

1. Search `Semantic Scholar` / `arXiv` / `CrossRef` API → fetch BibTeX by DOI.
2. Spawn an Agent subtask: "Verify BibTeX for [list of papers]; mark
   unverifiable as `% UNVERIFIED — verify before submission`."
3. Copy from an existing verified `refs.bib` in the worked-example papers.

Standard citation set for memory / agent-skills / hallucination papers
(use these as a starting point, **all verified**):

```bibtex
% --- ReAct, Reflexion, SELF-REFINE, Toolformer ---
yao2023react, shinn2023reflexion, madaan2023selfrefine, schick2023toolformer

% --- Skill libraries / memory ---
wang2024voyager, zhao2024expel, packer2023memgpt, xu2025amem,
zhong2024memorybank, wang2023longmem

% --- Self-evolution / RL skills ---
qi2024webrl, li2025webevolver, wang2025mobileagente, tang2025sage,
zhang2025skillrl

% --- Process rewards / self-taught ---
lightman2023letsverify, zelikman2022star

% --- Multi-agent / hallucination surveys ---
guo2024llmmas, huang2025hallucination, ji2023survey

% --- Anthropic Agent Skills ---
anthropic2025skills

% --- LLM-as-judge ---
zheng2023judging

% --- Benchmarks ---
maharana2024locomo (long-term memory)
```

---

## 11. Statistical rigor (per ml-paper-writing skill)

Every quantitative claim needs at least one of:

- **Paired McNemar's exact test** for binary correctness comparisons.
- **Wilcoxon signed-rank** for continuous metrics (MCS, EFS, ratings).
- **Bootstrap CIs** (5–10 seeds, resample with replacement, 1.96σ).
- **Inter-judge agreement** (Krippendorff's α) when using LLM-as-judge.

Implement once in `code/run_significance.py`, call from `make_paper.py`.

---

## 12. Reproducibility checklist (mandatory appendix)

Always include `appendix_repro.tex` with:

- [ ] Hyperparameters (learning rate, K, top-k, thresholds, ages)
- [ ] LLM settings (temperature, top_p, max_tokens, model version)
- [ ] Cache key formula (so re-runs reproduce exactly)
- [ ] Seeds (benchmark generation + bootstrap)
- [ ] Compute (total tokens, $$, wall-clock, high-level GPU/CPU class if relevant; no local device IDs or cache paths)
- [ ] Statistical methodology (which tests, two-sided, etc.)
- [ ] Code/data/prompts release plan with SHA-256 fingerprint of prompts

---

## 13. Common failure modes & fixes

| Symptom | Cause | Fix |
|---|---|---|
| Tables overflow column | Long row labels with `\citep{}` | Move citations to Related Work; use abbreviated labels in tables; `\resizebox{\columnwidth}{!}{...}` |
| Body > 8 pages | Too many top-floated figures | Move ≥3 figures to appendix; use `[ht]` not `[t]` |
| Method ties baseline | Need a new metric or a hybrid | Define AAcc-style metric; combine exemplar cache + your skill |
| Figure has typos | GenAI prompt unclear | Add "SPELL EXACTLY" and the correct text in quotes |
| matplotlib renders raw LaTeX | `\textbf{}` in axis labels | Use `fontweight="bold"` parameter, not LaTeX |
| API throughput too slow | Single thread vs multi-process | Launch independent methods in parallel processes |
| Cache miss explosion | Prompt nondeterminism | Keep `temperature=0`, fingerprint full prompt |
| ALFWorld outputs `[1,2,3]` | Format ambiguity | Add a one-shot example in prompt: `Output: ["go to ...", ...]` |

---

## 14. Tone & polish (Lipton / Perez / Gopen-Swan)

Apply these in a final language pass:

- **Eliminate hedging.** "may", "can", "could" → drop unless genuinely uncertain.
- **Active verbs.** "We performed an analysis" → "We analyzed".
- **Subject-verb proximity.** Don't separate them with relative clauses.
- **Stress at sentence end.** Put the punchline at the end: "When using
  attention, accuracy improves by **15 %**."
- **Delete fillers.** "actually," "basically," "very," "really,"
  "essentially," "quite."
- **Specific verbs over generic.** "performance improves" → "accuracy
  rises by 4 points".
- **Consistent terminology.** Pick one name for each concept, stick with it.

---

## 15. Submission preflight checklist

Before declaring "ready":

- [ ] PDF compiles with no `Undefined reference` or `Citation` warnings.
- [ ] No `Overfull \hbox > 5pt`.
- [ ] Body Sec 1–N (Conclusion) ends at or before page 8.
- [ ] No manual page break appears immediately before `\section{Conclusion}`.
- [ ] Limitations section present and ≥1 paragraph.
- [ ] Ethical Considerations section present.
- [ ] Every `\cite{}` resolves; no `[?]` in PDF.
- [ ] Every figure has a label and is referenced in text.
- [ ] Every table has a caption with a numerical headline.
- [ ] No `\textbf{[PLACEHOLDER]}` strings remain.
- [ ] Author names anonymised (`Anonymous EMNLP Submission`).
- [ ] `refs.bib` has 0 `% UNVERIFIED` entries (or you've explicitly told the
  user).
- [ ] At least one figure or table on each of pages 4–7 (not text-only
  pages with no visuals).
- [ ] At least one paired-significance table.
- [ ] Reproducibility appendix complete.

---

## 16. Worked example — one-line summary of each paper

**SHM-Gate** (historical reference — multi-agent memory paper):
- Problem: hallucination in multi-agent shared memory.
- Method: structurise + admission gate + hierarchical memory + correction loop.
- Benchmark: MA-MemConflict, 250 samples, 5 conflict types.
- Headline: 100 % accuracy across all 48 stress configurations vs baselines
  collapsing to 24 %; MCS 0.806 vs 0.667 Flat-RAG with $p<10^{-4}$.

**SkillCycle** (historical reference — skill self-evolution paper):
- Problem: self-generated agent skills hurt accuracy (SkillsBench
  negative finding).
- Method: executable verifier + dense retrieval + self-consistent compose
  + family-aware routing.
- Benchmark: a multi-source SkillEvolve-style matrix with several task families.
- Headline: SOTA on **Audited Accuracy** at K≤25 (0.958 with 19 verified
  skills vs 72 opaque exemplars). Replicates on gpt-4o backbone and
  ALFWorld 30-task subset (0.947 step-recall).

Both papers use the same: LaTeX skeleton, image-2 Figma-style non-data
figures, locally scripted data/metric/result figures, table-styling tokens,
statistical tests, EMNLP 8-page body limit, and citation discipline described
above.

---

## 17. Final advice

- **Spawn parallel agent subtasks** for independent experiments. Don't
  serialize 7 method runs.
- **Compile early, compile often.** Each section gets a `pdflatex` after
  it's drafted. Fixing 30 errors at the end is a nightmare.
- **The cache is your friend.** Once a method is "done", its results are
  immutable. Everything else (figures, tables, narrative) is iteration.
- **The metric you define controls who wins.** Picking the right metric
  is half the contribution.
- **Your method's name should describe what it does.** Avoid acronyms that
  don't expand to anything specific. SHM-Gate = "Structured Hierarchical
  Memory with Gated admission" — every word is load-bearing.
- **Honesty signals beat hype.** A clean, audited negative finding (or
  "ties at smaller cost") is more publishable than a 0.5 % overclaim.
- **Reuse infrastructure across papers.** `llm.py`, the table-styling
  tokens, the figure rcParams, the GenAI prompt template — all transfer.
