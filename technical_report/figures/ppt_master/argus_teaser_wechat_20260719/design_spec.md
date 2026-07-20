<!-- ppt-master-schema: design-spec/v1 -->
# argus_teaser - Design Spec

## I. Project Information

| Item | Value |
| --- | --- |
| Project Name | argus_teaser |
| Canvas Format | WeChat Article Header (900×383) |
| Page Count | 1 |
| Target Audience | Researchers and open-source readers scanning the first page of the Argus technical report |
| Communication Intent | Explain the recurrent Argus runtime and summarize its strongest cross-task results without mixing incompatible units |
| Desired Audience Outcome | A reader should understand the role loop, persistent shared state, and the breadth of measured outcomes in under one minute |
| Core Message / Ask / Action | Argus repeatedly plans, executes, reviews, and retains reusable state; the same runtime is evaluated across seven task-native result cards |
| Delivery Context | Reader-led, embedded at full text width on the paper's first page |
| Artifact Afterlife | Editable PPTX source, vector paper figure, archive, and future author hand-off |
| Reading Mode | text (read-close) |
| Content Strategy | User-authored radial structure: redraw the supplied Argus framework in English at the center and surround it on all four sides with exactly seven quantitative result plots |
| Design Style | Custom restrained anime-editorial infographic with native vector avatars, paper-like flat surfaces, precise rules, and compact data-journalism charts |
| Created Date | 2026-07-19 |

## II. Canvas Specification

| Property | Value |
| --- | --- |
| Format | WeChat Article Header |
| Dimensions | 900×383 |
| viewBox | `0 0 900 383` |
| Margins | 16 px outer safe margin |
| Content Area | 868×367 px; central 524×230 px framework with two top, two left, two right, and one bottom result plot |

## III. Visual Theme

### Theme Style

- **Mode**: briefing — one neutral, scannable page whose two halves carry equal scientific weight.
- **Visual style**: custom — flat editorial panels with crisp navy rules, restrained rounded corners, lightly hand-drawn anime role markers, compact chart labels, no shadows, no decorative AI imagery, and no oversized type.
- **Theme**: recurrent control on the left; task-native quantitative breadth on the right.
- **Tone**: rigorous, warm, concise, and publication-ready.

### Color Scheme

| Role | HEX | Purpose |
| --- | --- | --- |
| Background | `#FBF7EE` | Warm paper field |
| Surface | `#FFFDF8` | Cards and control-plane bands |
| Primary | `#24465D` | Main rules, headings, and body ink |
| Deep ink | `#173B70` | Quantitative emphasis and chart labels |
| Accent blue | `#315BCE` | Argus bars, planner marker, active feedback |
| Accent teal | `#287D70` | Engineer/reviewer validation and positive comparisons |
| Accent gold | `#C38A20` | Manager/stage authority and achievement markers |
| Muted text | `#66717D` | Secondary labels |
| Grid | `#D8E0E8` | Hairlines and card separators |

## IV. Typography System

### Font Plan

| Role | Chinese | English | Fallback tail |
| --- | --- | --- | --- |
| Title | Microsoft YaHei | Arial | sans-serif |
| Body | Microsoft YaHei | Arial | sans-serif |
| Emphasis | Microsoft YaHei | Arial Bold | sans-serif |
| Code | Microsoft YaHei | Consolas | monospace |

- Title: Arial Bold
- Body: Arial
- Emphasis: Arial Bold
- Code: Consolas

### Font Size Hierarchy

| Purpose | Size |
| --- | --- |
| Body | 15 px |
| Page title | 20 px |
| Subtitle / panel title | 16 px |
| Lead / card value | 18 px |
| Annotation | 12 px |
| Footnote | 10 px |

## V. Layout Principles

### Page Structure

- **Header area**: No page-level title; the two upper result plots occupy the top edge.
- **Content area**: The supplied four-layer Argus framework is centered. Six result plots flank its top, left, and right edges.
- **Footer area**: One wide Math-Reasoning result plot closes the ring below the framework.

### Spacing Specification

| Element | Current Project |
| --- | --- |
| Safe margin | 16 px |
| Content block gap | 8–12 px |
| Icon-text gap | 4 px |

## VI. Icon Usage Specification

| Purpose | Icon Path | Page |
| --- | --- | --- |
| Manager role marker | Native SVG avatar shapes; gold accent | P01 |
| Planner role marker | Native SVG avatar shapes; blue accent | P01 |
| Engineer role marker | Native SVG avatar shapes; teal accent | P01 |
| Reviewer role marker | Native SVG avatar shapes; navy/gold accent | P01 |

All markers are authored directly as editable SVG geometry; no external icon library or raster illustration is used.

## VII. Visualization Reference List

| Page | Template | Path | Summary-quote | Usage |
| --- | --- | --- | --- | --- |
| P01 | no-template-match | — | Custom cyclic architecture plus seven heterogeneous task-native small multiples | Catalog bar templates assume one shared metric, while this page requires independent scales, directions, and one stacked placement count |

Runners-up considered:
- `horizontal_bar_chart` | rejected for P01: it implies a single ranked scale across all seven tasks.
- `layered_architecture` | rejected for P01: it does not express the recurrent four-role state machine.
- `progress_bar_chart` | rejected for P01: several cards are lower-is-better measurements rather than completion percentages.

## VIII. Image Resource List

| Filename | Dimensions | Ratio | Purpose | Type | Layout pattern | Acquire Via | Status | Reference | text_policy | page_role |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

No external images are required. The supplied framework is translated and redrawn as native vector structure rather than embedded as a screenshot.

## IX. Content Outline

### Part 1: Framework and headline evidence

#### Slide 01 - Argus recurrent runtime and seven task-native results

- **Audience move**: Move from recognizing the control mechanism to trusting the breadth and scope of the reported evaluations.
- **Cover impact**: A faithful English redraw of the supplied framework occupies the visual center while seven bar-chart results form a surrounding evidence ring.
- **Layout**: Central framework x=188–712, y=76–306. Results: two top, two left, two right, and one bottom. Thin connectors point inward without implying a shared numerical scale.
- **Title**: No page-level title inside the figure; titles are limited to framework modules and benchmark names.
- **Core message**: Manager, Planner, Engineer, and Reviewer form a recurrent stage loop around persistent control and shared state; seven result cards show task-native outcomes without cross-normalization.
- **Content**:
  - Left input capsule: `Objective + evaluator`.
  - Four-role loop around `Stage k`: Manager — `stage gate`; Planner — `next mission`; Engineer — `build + test`; Reviewer — `independent check`.
  - Manager outcome capsule: `Advance · hold · roll back`.
  - Feedback caption: `retained state → next mission`.
  - Structured control band: `ReviewDecision · StageDecision · Mission View`.
  - LifeSupervisor band: `backlog · budget · daemon · recent memory`.
  - Shared workspace band with three cells: `Wiki + skills`, `Events + decisions`, `Code + results`.
  - Card 1, `SWE-Bench Pro`: horizontal bars on a 0–100% scale; Direct Copilot 59%, Argus 78%; separate badge `1.41× tokens`; higher is better.
  - Card 2, `nanochat B200`: two horizontal bars on a labeled 0.960–0.966 BPB axis; Argus 0.9636, human 0.9646; lower is better.
  - Card 3, `nanochat H100`: two horizontal bars on a labeled 0.984–0.989 BPB axis; Argus 0.9855, human 0.9879; lower is better.
  - Card 4, `AARRI-Bench`: horizontal bars on a 0–100% scale; Argus 76.8%, paper best 68.3%; higher is better.
  - Card 5, `SOL-ExecBench`: one seven-placement stacked bar with a blue segment for 2 wins and a teal segment for 5 additional top-3 finishes; labels `Global #6` and `101 kernels`.
  - Card 6, `nanoGPT speedrun`: two horizontal bars on a labeled 79.5–80.5 s axis; Argus 79.77 s, human 80.18 s; lower is better.
  - Card 7, `Math-reasoning data`: four bars on a 0–30 gap scale; Argus 28.0, Arbor 20.83, Claude Code 8.33, Codex 6.25; higher is better.
  - Footer: `Independent scales · arrows show metric direction · values retain source units`.
  - Data sources: `technical_report/evidence/website_results.json` and `technical_report/evidence/swebench_pro/unified_experiment_summary.json`.

## X. Speaker Notes Requirements

- **Filename**: match each SVG filename under `notes/`
- **Content**: One concise paragraph explaining that the left side is schematic, the right side uses independent task-native scales, and lower-is-better cards use explicitly labeled truncated axes.
