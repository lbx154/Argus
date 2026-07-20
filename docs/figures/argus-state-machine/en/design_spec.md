<!-- ppt-master-schema: design-spec/v1 -->
# argus_state_machine_en - Design Spec

## I. Project Information

| Item | Value |
| --- | --- |
| Project Name | argus_state_machine_en |
| Canvas Format | PPT 16:9 (1280×720) |
| Page Count | 2 |
| Target Audience | Argus architects, agent-systems researchers, top-tier conference reviewers, and engineering leads |
| Communication Intent | Explain the repaired Argus transition system and show that every previously identified deadlock, livelock, and bounded stall now has an explicit exit mechanism |
| Desired Audience Outcome | Readers can recover role authority, normal transitions, liveness guards, and safe terminal states after two-column scaling |
| Core Message / Ask / Action | The repaired state machine closes every known loop through rejection, wake-up, atomic commit, bounded retry, or human escalation |
| Delivery Context | Figure set for a top-tier systems or machine-learning conference paper; reader-led with optional oral explanation |
| Artifact Afterlife | Main paper, appendix, architecture review, and repair-design baseline |
| Reading Mode | text |
| Content Strategy | Re-architect the audit into two standalone paper figures while preserving all verified state semantics and risk classifications |
| Design Style | SOSP/OSDI data-journalism architecture figure; restrained color, colorblind-safe semantics, grayscale distinguishability |
| Created Date | 2026-07-20 |

## II. Canvas Specification

| Property | Value |
| --- | --- |
| Format | PPT 16:9 |
| Dimensions | 1280×720 |
| viewBox | `0 0 1280 720` |
| Margins | 40 px |
| Content Area | 1200×640 px |

## III. Visual Theme

### Theme Style

- **Mode**: instructional
- **Visual style**: data-journalism
- **Theme**: Academic systems architecture with semantic state colors
- **Tone**: Precise, analytical, evidence-led, publication-ready

### Color Scheme

| Role | HEX | Purpose |
| --- | --- | --- |
| Background | #FBFCFE | Paper field with reduced glare |
| Secondary background | #F1F5F9 | Lanes, groups, and explanatory regions |
| Primary | #25364A | Titles, node labels, and borders |
| Control flow blue | #4477AA | Manager/Planner control chain |
| Normal progress green | #228833 | Valid progress and successful transitions |
| Recoverable wait amber | #CCBB44 | HOLD, pause, and recoverable waiting |
| Critical loop red | #EE6677 | Deadlocks, livelocks, and failure cycles |
| Operator arbitration purple | #AA3377 | Human authority and Manager arbitration |
| Inactive gray | #BBBBBB | Inactive roles and background edges |
| Body text | #25364A | Body copy and annotations |

Color is never the sole encoding: solid edges denote normal transitions, dashed edges denote waits, double/looping edges denote deadlocks, and dash-dot edges denote human arbitration.

## IV. Typography System

### Font Plan

| Role | Chinese | English | Fallback tail |
| --- | --- | --- | --- |
| Title | Microsoft YaHei | Arial | sans-serif |
| Body | Microsoft YaHei | Arial | sans-serif |
| Emphasis | Microsoft YaHei | Arial | sans-serif |
| Code | Consolas | Consolas | monospace |

- Title: Arial Bold
- Body: Arial
- Emphasis: Arial Bold
- Code: Consolas

### Font Size Hierarchy

| Purpose | Size |
| --- | --- |
| Body | 20 px |
| Page title | 36 px |
| Subtitle | 24 px |
| Annotation | 15 px |
| Footnote | 13 px |

## V. Layout Principles

### Page Structure

- **Header area**: 40–92 px, left-aligned title and one-line claim; figure ID and legend on the right
- **Content area**: 92–674 px, asymmetric regions on a precise grid; avoid card-wall repetition
- **Footer area**: 674–704 px, short caption, state symbols, and provenance note

### Spacing Specification

| Element | Current Project |
| --- | --- |
| Safe margin | 40 px |
| Content block gap | 16–22 px |
| Icon-text gap | N/A (no icons) |

## VI. Icon Usage Specification

No decorative icons. State semantics are expressed through geometry, line style, arrows, and color.

## VII. Visualization Reference List

| Page | Template | Path | Summary-quote | Usage |
| --- | --- | --- | --- | --- |
| P01 | layered_architecture | templates/charts/layered_architecture.svg | "Pick for 3-4 horizontal architecture layers (presentation/service/data), 2-4 module cards per layer, each card = title + 1-line description (description required, even if source brief). Skip if no per-module descriptions (use icon_grid) or no horizontal layering (use module_composition)." | Adapt into control authority, execution loop, backlog reconciliation, and persistent-state wake layers |
| P02 | no-template-match | N/A | N/A | Custom liveness-safeguard atlas mapping each former risk to its guard and exit state |

Runners-up considered:
- process_flow | rejected for P01: cannot express Reviewer–Engineer cycles and stage rollback.
- circular_stages | rejected for P02: assumes one benign closed loop rather than multiple failure SCCs.
- matrix_2x2 | rejected for P02: risk classes are categorical rather than two-axis measurements.

## VIII. Image Resource List

No images. All visible content is authored as native SVG text, shapes, paths, and connectors.

## IX. Content Outline

### Part 1: Repaired Argus State Machine and Liveness Guards

#### Slide 01 - Repaired Argus State Machine: Every Wait Has an Exit Edge

- **Audience move**: Move from role authority to verification that every persistent state, replan branch, and stage ruling reaches a safe successor
- **Layout**: Three horizontal architecture layers. Top: Operator/Manager/Planner control plane. Middle: Backlog Reconciler and Engineer↔Reviewer loop. Bottom: stage, pause, daemon-upgrade, and operator-answer persistence. Right-side legend, model-spend scope, and liveness invariants.
- **Title**: Repaired Argus State Machine: Every Wait Has an Exit Edge
- **Core message**: Same-scope repair remains inside the Engineer–Reviewer loop; all scope changes, waits, and failures now terminate through versioned CAS, automatic wake-up, bounded circuit breaking, or Manager/Operator arbitration.
- **Content**:
  - Operator → Manager: Manager interprets objective, authorization, and workflow; only Manager may ADVANCE, HOLD, or ROLLBACK a stage.
  - Planner → atomic DAG commit: cycle validation runs before the whole batch is written; ordinary plans and replacements cannot partially commit.
  - Both `next_pending` and `claim_next` run backlog reconciliation: legacy SCCs, missing dependencies, and failed dependencies become `skipped` instead of appearing as an empty queue.
  - Engineer → Reviewer; `continue_same_scope` enters the next Engineer round while preserving Reviewer evidence.
  - `replan_requested` carries `plan_id + plan_version`; replacement is committed by compare-and-swap and old nodes become non-resurrectable `superseded` states.
  - Manager HOLD no longer rearms the same bounded item; a new daemon intent reopens a terminal workspace stage instead of declaring the new task complete.
  - `paused_budget/provider/daemon_shutdown` automatically starts a new attempt when the condition clears; `paused_operator` remains explicitly human-controlled.
  - An Operator answer creates a continuation and atomically rewires every live downstream dependency from the old failed ID.
  - The UI now labels cost as `MODEL SPEND`: model/API calls only, excluding GPU and infrastructure.

#### Slide 02 - Liveness Safeguard Atlas: How All 13 Risks Are Closed

- **Audience move**: Move from awareness of risk to verification of each guard, exit state, and test artifact
- **Layout**: Normal progression and safe terminal spine on the left; P0/P1/P2 columns on the right, each mapping former risk → guard → exit state. Bottom strip contains validation evidence and the unified liveness condition.
- **Title**: Liveness Safeguard Atlas: How All 13 Risks Are Closed
- **Core message**: Every previously identified deadlock or livelock component now has at least one deterministic exit; repeated failure cannot consume unbounded budget.
- **Content**:
  - P0-1 DAG cycle: reject new cyclic batches; terminate legacy SCCs as `skipped` during reads.
  - P0-2 Unreachable cleanup: `next_pending` and `claim_next` share reconciliation, so cleanup runs even when no node is ready.
  - P0-3 Unversioned replan: `expected plan/version → CAS → version+1`.
  - P0-4 Fully filtered replacement: persist attempts; the third rejection opens a circuit breaker and fails the current node closed.
  - P0-5 Recoverable pause: budget, provider, and daemon pauses auto-resume with a fresh attempt; operator pause remains manual.
  - P0-6 Operator-answer disconnection: atomically rewire all live downstream dependencies to the continuation.
  - P1-1 Stage lifecycle: HOLD terminates the current bounded item, while a new intent reopens a completed stage.
  - P1-2 Drain/upgrade: zero-block drain request plus a five-second scheduled recheck; healthy long experiments continue to a natural boundary.
  - P1-3 Unresolved cost: conservatively hold the reservation amount without blocking the whole host.
  - P1-4 Manager feedback: three identical-evidence attempts maximum, then terminal idle; Operator input or a new artifact revision wakes planning.
  - P1-5 Completed-task dedup: signatures include acceptance, scope, and context hash, allowing a legitimate rerun after evidence changes.
  - P2-1 Lock order: canonical `pipeline → session` acquisition.
  - P2-2 Planner failure: no-runner and exception paths use fifteen-to-three-hundred-second exponential backoff.
  - Validation evidence: expanded life/manager/daemon suite passed; concurrent claim, concurrent answer, and filtered-replan stress passed for twenty consecutive rounds; TUI passed 189 tests and Web passed 107 tests.

## X. Speaker Notes Requirements

- **Filename**: match each SVG filename under `notes/`
- **Content**: 90–150 seconds of formal academic narration per figure; explain color and line semantics, distinguish proven deadlocks from livelocks and intentional waits, and introduce no claims beyond the audit.
