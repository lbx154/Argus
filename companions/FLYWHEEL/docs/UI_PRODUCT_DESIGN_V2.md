# ARGUS / FLYWHEEL — UI and product map v2

## Brand decision

The first-party product name is **Argus Flywheel**. The interface renders it as
`ARGUS / FLYWHEEL` with the descriptor `Research operations`.

`Benchmaker` is useful, but too narrow for the whole product: Flywheel also schedules
venues, conditions ideas, supervises Argus, reviews evidence and closes the outcome
loop. **Benchmaker** therefore names the governed annotation and dataset flywheel inside
Context Studio and Outcomes. It never implies that export automatically trains a model.

## One product, four jobs

```text
PLAN                     RUN                      REVIEW                  LEARN
Horizon → Context → Ideas → Campaigns → Viewer → Approval → Outcomes → next condition
      venue + team fit       Argus execution       human gates          Benchmaker data
```

This research spine is the signature element of the shell. It is navigation and state
orientation, not decoration. The current route lights the corresponding job; system
routes keep the last research job visible and identify themselves as infrastructure.

## Page contract

| Page | Question answered | Primary action | What belongs here | What does not |
| --- | --- | --- | --- | --- |
| Evidence Horizon | What needs attention now? | Inspect a venue and begin a conditioned brief | 12-month deadlines, official/forecast evidence, urgency, active campaigns | Deep run control |
| Context Studio | Who is doing the work, with what rights and limits? | Freeze a team-conditioned ideation objective | expertise, methods, data permission, compute/time, goals, policy, source binding | Generic venue-only prompts |
| Idea Radar | Which falsifiable candidates fit this team and venue? | Compare, label or promote a candidate | fresh-source deltas, Builder/Breaker/Arbiter output, novelty uncertainty, scalar/pairwise labels | Claims of guaranteed novelty |
| Campaigns | Is Argus making inspectable progress? | Open or launch an approved bounded campaign | portfolio, stage, work graph, evidence progress, budget, artifacts, prompt revision | Automatic submission |
| Argus Viewer | Would independent reviewers continue, replan or stop? | Queue an evidence-bounded review | separate-process reviews, disagreements, blockers, citations, reproducibility | Acceptance probability |
| Approval Inbox | Which irreversible or high-risk choice needs a person? | Approve, revise or reject with rationale | lock/start/release/rebuttal gates and impact | Passive notifications |
| Outcomes & Rebuttal | What happened, and what should the system learn? | Record outcome or create an idle rebuttal follow-up | versions, reviewer feedback, scores, rebuttal, consented Benchmaker records | Contacting reviewers or submitting |
| Connections | Which real Argus and source systems are trusted? | Test a connection or stage a verified release | WebAPI identity, source adapters, auth state, exact release SHA | In-place update of a running checkout |
| Resources & Settings | What capacity and preferences govern this workspace? | Configure compute, models, notifications and appearance | pools, backend roles, reminders, theme, language, release policy | Pretending a config is live telemetry |

## Information architecture

```text
┌ ARGUS / FLYWHEEL ───────────────────────────────────────────────────────────┐
│ search                                    runtime  language  theme  alerts │
├───────────────┬────────────────────────────────────────────────────────────┤
│ PLAN          │ PLAN ───────── RUN ───────── REVIEW ───────── LEARN       │
│  Horizon      ├────────────────────────────────────────────────────────────┤
│  Context      │ page title                              one primary action │
│  Ideas        │ short purpose / truth boundary                            │
│               │                                                            │
│ RUN           │ priority / next decision                                  │
│  Campaigns    │ ┌──────────────── main work surface ────────────────────┐ │
│  Viewer       │ │ timeline, graph, ledger, editor or review             │ │
│               │ └────────────────────────────────────────────────────────┘ │
│ DECIDE        │ optional inspector / evidence provenance                  │
│  Approvals    │                                                            │
│  Outcomes     │                                                            │
│ SYSTEM        │                                                            │
│  Connections  │                                                            │
│  Settings     │                                                            │
└───────────────┴────────────────────────────────────────────────────────────┘
```

Desktop uses a compact grouped rail and one dominant content surface. Mobile uses a
drawer plus horizontally scrollable stage/context controls; dense graphs and ledgers
may scroll, while approval and current state stay directly usable.

## Visual system

The palette preserves Flywheel's independent Argus lineage and is expressed through
semantic tokens in both themes:

| Named color | Dark | Light | Role |
| --- | --- | --- | --- |
| Lab Night / Paper | `#0c1017` | `#f4f7fb` | canvas |
| Graphite / White | `#141a24` | `#ffffff` | primary surface |
| Argus Iris | `#72a7ff` | `#245fb8` | action, focus, current stage |
| Evidence Mint | `#75ddb1` | `#087b58` | verified evidence and healthy state |
| Decision Amber | `#f2c66d` | `#9b6300` | forecast, attention, human decision |
| Integrity Coral | `#ee8e86` | `#b23a35` | operational/integrity failure only |

Typography uses Geist Sans for interface copy and Geist Mono only for SHA, time,
metrics, version and machine state. Page titles are 28–36px, sections 16–22px, body
13–14px, metadata 11–12px. Nine-pixel text is reserved for compact hashes and never
used for explanations.

Surfaces use 8/12/16px radii, a restrained one-pixel border and spacing before shadow.
Nested cards are flattened into sections. Motion is 140–220ms; continuous flow appears
only when a real run is active and all motion respects `prefers-reduced-motion`.

## Language and theme contract

- Locales: Simplified Chinese and English.
- Themes: dark, light and system.
- Preferences persist locally and update `html.lang` / `html[data-theme]` before the
  workspace is used.
- Shell, page identity, settings and primary actions are translated first; scientific
  user/data content remains verbatim.
- Theme and language are available in the top bar and in Resources & Settings.

## Reference adaptation and critique

The layout grammar is informed by Airesearch's Mission Control: grouped navigation,
clear work surfaces, stage visibility, inspector patterns and complete empty/error/live
states. Its green palette, generic hero treatment and any hard-coded chart colors are
not copied. Flywheel's unique visual and conceptual anchor is the research spine tied to
real Argus stages, evidence gates and the Benchmaker feedback loop.
