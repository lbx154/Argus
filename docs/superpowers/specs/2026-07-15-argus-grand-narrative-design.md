# Argus Grand Technical Narrative Design

**Date:** 2026-07-15  
**Status:** Approved design  
**Surfaces:** Technical Report 0.3, `README.md`, `README.zh-CN.md`  
**Visual reference:** `https://argusbot.cn/`

## Purpose

Rebuild the Argus technical report and GitHub landing pages around one memorable
technical story:

> **Argus sustains dense research intelligence, turns each run's evidence into
> runtime capability, and carries that capability into the next unknown task.
> Every run expands the frontier.**

The current report accurately documents many subsystems, but its architecture,
runtime, state, evidence, results, and portfolio sections have nearly equal
narrative weight. The redesign must preserve their technical content while
making them serve one causal chain:

```text
continuous dense intelligence
    -> auditable research evidence
    -> runtime capability evolution
    -> evidence-gated OOD frontier expansion
    -> the next run begins from a stronger operating state
```

The website is the visual and narrative source of truth for this direction. The
report must translate its blue-gold frontier field, Life loop, dense-intelligence
thesis, and work-to-learning cycle into a restrained systems whitepaper rather
than copying its animated marketing treatment.

## Audience

The report remains technical-reader-first while also serving research leaders,
industry partners, investors, and academic reviewers. A systems reader must be
able to trace every thesis to interfaces, state, code paths, and evidence. A
non-specialist technical reader should be able to retain the central story after
reading only the cover, executive thesis, master figure, and result section.

## Scope

### In scope

- Rewrite the Technical Report 0.2 narrative as Technical Report 0.3.
- Preserve the title `Argus: Autonomous Research Generation and Understanding
  System`.
- Change the subtitle to `Dense Intelligence for an Expanding Research
  Frontier`.
- Consolidate the report into four acts and thirteen main chapters.
- Introduce one master technical figure containing the four roles.
- Formalize dense intelligence, online runtime evolution, and evidence-gated
  capability expansion as explanatory mathematical objects.
- Reframe the six benchmark arenas and 41-paper portfolio as frontier proof
  points without changing any reported number or evidence status.
- Rewrite the English and Chinese README pages around the same narrative.
- Translate the website's light blue-gold visual language into the report cover,
  act openers, figures, and README hero.

### Out of scope

- No changes to `argusbot.cn`.
- No runtime, scheduler, agent-role, benchmark, or provider behavior changes.
- No new benchmark claims, paper-acceptance claims, or capability guarantees.
- No claim that model parameters update online.
- No claim that every run succeeds or that capability grows monotonically in
  practice.
- No dark report cover.

## Core Technical Thesis

### Problem

Research intelligence is not limited only by the quality of a single reasoning
step. It is also limited by how sparsely useful reasoning, execution, and
verification occur over wall-clock time, how much context must be reconstructed
between episodes, and whether the resulting experience becomes durable
organizational capability.

Human research can be extremely high quality but is naturally sparse in time.
Short-lived agents can execute quickly but often treat each task as an isolated
episode. Unbounded single-session agents accumulate stale context and role
conflict. None of these conditions automatically produces compounding research
capability.

### Argus response

Argus Life sustains a continuous loop of decision, execution, and independent
verification. Manager, Planner, Engineer, and Reviewer organize that loop. The
control, execution, and evidence planes make it persistent and auditable.
Research artifacts, measurements, failures, counterexamples, review verdicts,
and process traces become inputs to bounded memory, skills, tools, verifiers,
routing, evaluations, and reusable knowledge.

This is online **runtime evolution**, not online model training. The underlying
model remains selectable and may remain unchanged while the operating state
around it evolves.

### Frontier consequence

When a retained capability is supported by evidence, later work can reuse it.
The next OOD task therefore need not begin from an empty operating state. The
system is designed to expand the breadth of tasks it can enter and the depth of
work it can perform within a domain, subject to evidence and resource limits.

The recurring sentence across the report and README is:

> **Every run leaves the system more capable of the next unknown task.**

This sentence describes the design objective. It does not promise that every run
produces a verified capability addition.

## Formalization

The report introduces three explanatory objects. They are conceptual models,
not newly reported empirical metrics.

### 1. Dense-intelligence density

```text
rho_DI(T) = (1 / T) integral_0^T
            lambda(t) eta_d(t) eta_x(t) eta_v(t) dt
```

- `T`: wall-clock research horizon.
- `lambda(t)`: rate at which relevant reasoning and tool-mediated research work
  is attempted.
- `eta_d(t)`: decision quality/utility factor.
- `eta_x(t)`: execution effectiveness factor.
- `eta_v(t)`: verification effectiveness factor.

The equation explains why token volume alone is not research value. Continuous
activity contributes only to the extent that it produces useful decisions,
effective execution, and credible verification. The report must not publish
`rho_DI` as a measured Argus score or claim a universal inequality against human
researchers.

### 2. Online runtime evolution

```text
H_t = {M_t, S_t, A_t, V_t, R_t, Q_t}
H_(t+1) = U(H_t, tau_t, E_t)
theta_(t+1) = theta_t
```

- `M`: bounded and long-term memory.
- `S`: reusable skills.
- `A`: available tools and tool-use procedures.
- `V`: verifiers and evidence procedures.
- `R`: routing and role configuration.
- `Q`: evaluations and task definitions.
- `tau_t`: the process trajectory from run `t`.
- `E_t`: the evidence accepted from that run.
- `theta`: model parameters.

The fixed-`theta` line is mandatory. It prevents runtime adaptation from being
misread as parameter training.

### 3. Evidence-gated capability expansion

```text
C_(t+1) = C_t union {c : Verify(c, E_t) >= epsilon}
```

This is set semantics for retaining verified additions. It does not assert that
the system's total practical ability is empirically monotone, that additions
cannot later be retired, or that failed runs add a positive capability. Negative
results may still add reusable knowledge about dead ends.

## Master Technical Figure

The master figure appears immediately after the executive thesis and is reused
as the README hero image. Its left-to-right causal spine is:

```text
Unknown objective
    -> Dense Intelligence Runtime
    -> Evidence Gate
    -> Runtime Evolution
    -> Expanded OOD Frontier
    -> next unknown objective
```

The `Dense Intelligence Runtime` box contains all four roles:

| Role | Label in the master figure | Function in the story |
|---|---|---|
| Manager | intent · lifetime · stage | Maintains objective and lifecycle coherence |
| Planner | decompose · schedule · re-plan | Converts frontier uncertainty into executable work |
| Engineer | retrieve · build · experiment | Produces real actions and artifacts |
| Reviewer | inspect evidence · decide | Determines whether work changes the accepted state |

The `Runtime Evolution` box contains `Memory`, `Skills`, `Tools`, `Verifiers`,
`Routing`, and `Evaluations`. The figure includes the three formal objects below
the causal spine and a visible qualification that the equations are explanatory,
not measurements.

The detailed three-plane architecture remains later in the report. It explains
implementation ownership; it no longer competes with the master figure for the
report's central meaning.

## Report Information Architecture

The report target is 28–30 pages. It must not omit content needed to support its
claims merely to satisfy the page limit. It uses four acts and thirteen main
chapters, numbered 1 through 13 below.

### Act I — The Dense-Intelligence Problem

1. **Executive Thesis**  
   State the entire causal chain, define the report's scope, present the master
   figure, and summarize the evidence without opening on limitations.

2. **Research Intelligence Is Sparse in Time**  
   Define dense-intelligence tasks, distinguish continuity from raw token volume,
   and explain why wall-clock organization matters.

3. **Why Episodic Agents Do Not Compound**  
   Cover context degradation, role conflict, lost process data, sparse
   verification, and the difference between model capability and system
   capability.

### Act II — The Machine That Turns Work into Capability

4. **Argus Life and the Technical Spine**  
   Explain the Life loop and map each part of the master figure to implemented
   runtime machinery.

5. **Four Roles, Three Planes, One Research Runtime**  
   Present role decision boundaries, control/execution/evidence ownership, and
   the Reviewer completion authority.

6. **Mission Lifecycle, State, and Bounded Context**  
   Cover backlog, claim, Engineer–Reviewer rounds, `checkpoint.json`, bounded
   session reuse, persisted identity, resume, pause, block, and re-plan.

7. **Evidence, Measurement, and Independent Review**  
   Cover typed events, execution-log audit, benchmark-specific integrity,
   evidence categories, artifact digests, and provenance.

8. **Reliability, Resources, and Operator Control**  
   Consolidate recovery, budgets, provider failures, background jobs, GPU lease,
   cockpit, API, deployment, and human intervention boundaries.

### Act III — Online Runtime Evolution

9. **From Trajectories to Runtime State**  
   Explain `H_t`, checkpoint curation, skill lifecycle, wiki, tools, verifiers,
   routing, and evaluations. State which updates are automatic, model-proposed,
   Reviewer-authored, or operator-invoked.

10. **Process Data as the Compounding Asset**  
    Distinguish a final artifact from the trajectory that produced it. Show how
    decisions, failed attempts, evidence, and feedback support later work.

11. **Evidence-Gated OOD Expansion**  
    Explain capability retention, cross-task reuse, vertical expansion, and why
    negative results and NO-GO decisions can still improve future search.

### Act IV — Evidence from the Frontier

12. **Six Arenas and Six Research Programs**  
    Present the six public benchmark results and the 41-paper portfolio as scoped
    proof points across tasks and domains. Keep protocols, human/paper references,
    website-snapshot labels, and artifact-digest labels unchanged.

13. **Limitations, Failure Modes, and Roadmap**  
    Cover model dependence, single-Reviewer risk, evidence gaps, benchmark-specific
    integrity, cost, non-monotone practical capability, and the path to stronger
    external reproducibility.

### Appendices

- Key interfaces and complete status taxonomy.
- Persisted state map.
- Event taxonomy.
- Claim-to-source evidence map.
- Public result and paper-inventory summaries.
- Formal notation table.

## README Information Architecture

The English README target is 1,200–1,600 prose words. The Chinese README carries
the same thesis and evidence boundaries without needing sentence-level literal
translation.

1. **Hero** — `Every run expands the frontier.` One paragraph defines dense
   intelligence, evidence, runtime evolution, and the next unknown task.
2. **Master loop** — use the master technical figure.
3. **Dense Intelligence** — a concise definition and the fixed-parameter runtime
   evolution distinction.
4. **Frontier proof points** — six arena results and 41 outputs, with unchanged
   protocols and evidence labels.
5. **How the system makes the loop real** — four roles, three planes, bounded
   state, review, and reliability in a compact section.
6. **Quick start and report link** — installation, supported backends, limitations,
   and direct Technical Report 0.3 link.

Detailed API lists, event counts, full state maps, and extended operational
explanations belong in the report rather than the README.

## Visual System

### Palette

- Bone-white paper field: `#FBFAF6`.
- System blue: `#315BCE`.
- Deep report blue: `#214884`.
- Verified-frontier gold: `#C38A20`.
- Graphite body text and rules.

Blue represents runtime structure, organization, continuity, and control. Gold
represents verified evidence, retained capability, and frontier expansion. Gold
must not imply success before verification.

### Typography

- Serif display face for thesis statements, act titles, and figure headlines.
- Sans-serif body face for readable engineering prose.
- Monospace for telemetry, protocols, evidence labels, version strings,
  interfaces, and equations.

### Cover

The cover is light, not dark: bone-white field, thin blue-gold orbit geometry,
large negative space, blue title, gold telemetry accents, and `Technical Report
0.3`. It must print cleanly and remain legible in GitHub previews.

### Interior hierarchy

- Each act opens with one large thesis, one short explanatory paragraph, and one
  compact causal flow.
- Existing repeated `Design objective` boxes are reduced or replaced by act-level
  thesis openers.
- Technical detail remains dense, but every table and subsection must visibly
  support the act thesis.

### Figures

- Add the master technical spine in PDF and PNG.
- Add or revise a dense-intelligence continuity figure without claiming measured
  superiority over humans.
- Revise the mission lifecycle figure to connect evidence output to runtime-state
  update.
- Keep the public-results and paper-portfolio figures with unchanged data.
- Keep the detailed three-plane architecture as a secondary implementation
  figure.
- Remove or demote figures that duplicate the new master narrative.
- Record deterministic figure hashes in `REPORT_FIGURES.json`.

## Claims Discipline

- Preserve all six public result values, protocols, references, and evidence
  statuses exactly.
- Preserve `41 = 35 manuscripts + 6 drafts`; never imply acceptance.
- Keep human/public references primary where available; preserve source-verbatim
  system comparisons where the website provides them.
- Keep `checkpoint.json`, bounded session reuse, event counts, defaults, auth,
  budgets, and deployment claims grounded in the committed source at the
  implementation base.
- Label dense intelligence as a formal explanatory construct, not a measured
  benchmark.
- Label runtime evolution as state around the model, not parameter learning.
- Do not use `unfixed ceiling` as a guarantee. It may appear only as a design
  aspiration paired with evidence and resource limits.
- Do not claim monotone practical capability growth.
- Keep `guardrail(s)` at no more than two occurrences; keep `dumb pipe` and
  `plumbing` at zero.

## File-Level Impact

Expected implementation surfaces:

- `README.md`
- `README.zh-CN.md`
- `technical_report/main.tex`
- `technical_report/references.bib` only if new literature is actually cited
- `technical_report/sections/*.tex` — consolidate into the thirteen-chapter
  structure
- `technical_report/figures/build_report_figures.py`
- generated deterministic PDF/PNG figures
- `technical_report/figures/REPORT_FIGURES.json`
- `technical_report/argus-technical-report.pdf`
- focused documentation/figure tests

The implementation must not modify Argus runtime code to make the narrative true.
If a proposed sentence is unsupported, the sentence changes.

## Acceptance Criteria

### Narrative

- Cover, executive thesis, master figure, all four act openers, conclusion, and
  README repeat the same causal spine.
- A reader can state the main story without naming an internal module.
- Four roles and three planes support the story rather than replace it.
- Results are presented as evidence of the frontier thesis, not as an unrelated
  leaderboard section.

### Content

- Four acts and thirteen main chapters are present.
- Report length is 28–30 pages.
- English README contains 1,200–1,600 prose words.
- Chinese README has matching structure and claims.
- Every notation symbol is defined in text or a notation appendix.
- All six result rows and 41-paper totals match the evidence JSON files.

### Build and visual quality

- `make -C technical_report clean all` exits successfully.
- No overfull boxes.
- No undefined citations or references.
- Every PDF page is visually inspected.
- Cover, act openers, tables, equations, and figures remain legible at normal
  zoom and in print.
- Deterministic figures reproduce byte-identically in the chosen environment and
  their manifest hashes match.
- README local links resolve.

### Review

- One independent ML-systems review checks every architecture and runtime claim
  against the committed code.
- One independent claims/citations/visual review checks results, portfolio,
  equations, evidence labels, hashes, and every PDF page.
- All Critical and Important findings are fixed and re-reviewed before push.
