# V5 Vertical Improvement Plan

**Date:** 2026-07-14 | **Basis:** V4_FULL_GATE_VALIDATION_REPORT, V4_CAPABILITY_USAGE_AUDIT,
TIMEOUT_LIMIT_AUDIT, and issue 六 (over-defensive manuscript). **Constraint:** focused, testable
additions — no big rewrite; protected pipeline stays intact; all changes covered by smoke tests.

## Problems this addresses
- Caps are **exposed not executed** (0% enforced consumption) → traceability + execution.
- Gates check only at **stage exit** → agent runs-then-repairs → add **stage-entry contracts**.
- No **Novelty-Seeking Loop** → benchmark/reproduction becomes the easy terminal → add loop + mode.
- Manuscript is **over-hedged** (issue 六): disclaimers repeated in every section instead of physical
  meaning → add anti-over-hedging + idea-centric rule (mirror upstream `81535928`).
- Outer `timeout 3600` killed a healthy mission → progress-aware watchdog, no fixed wall clock.

## Improvements

### 1. Gate-forward: stage-entry contracts (`stages.py`)
Add `stage_entry_contract(stage)` returning, per stage: **required artifacts**, **minimum standard**,
**claim constraints**, **forbidden overclaim**. Inject the contract for the *current* stage (read from
`research/PIPELINE_STATE.json`) into `role_banner`, so the agent gets the gate standard **before**
doing the work, not only the failure list after. Stage-exit verifiers (existing gates) unchanged.

### 2. Capability consumption trace (`skills/capability_trace.py` + gate hooks)
New helper writes/merges `research/CAPABILITY_CONSUMPTION_TRACE.json` with one record per gate:
`gate, available_count, exposed_capability_ids, applicable_capability_ids, selected_capability_ids,
used_capability_ids, evidence_files, failure_ids_caused_by_missing_capability,
repair_actions_triggered, claim_changes_caused, paper_type_effect`. Each gate calls it on run.
Promotes numerical/theory/novelty families toward **executed** checks (bind failure codes to the
artifact fields the capability requires).

### 3. Novelty-Seeking Loop (`gates/novelty_seeking.py` + banner)
Advisory gate, ACTIVE only in original-research-required mode. Before the terminal manuscript it
requires: `NOVELTY_IDEA_POOL.md/.csv`, `PIVOT_SELECTION.md`, `REVISED_RESEARCH_OBJECTIVE.md`,
`ADDITIONAL_THEORY_PLAN.md`, `ADDITIONAL_NUMERICAL_PLAN.md`. Verifies the pool has **≥10 directions**,
each with `closest_prior_work, already_known, possible_gap, why_physically_meaningful,
minimal_theory_check, minimal_numerical_experiment, expected_evidence_artifact, risk_of_already_known,
kill_criterion` and scores (`novelty_potential, prior_work_separation, physical_significance,
feasibility, evidence_clarity, risk_of_already_known`); **top 2–3 selected**; extra theory/numerical
verification done. If still not original after verification → **must pivot (≤2 rounds)**; still not →
`ORIGINAL_RESEARCH_NO_GO.md` explaining why. NSL failure codes NSL-000..00N.

### 4. Original-research-required mode (config + `gates/paper_type.py` + `manuscript.py`)
Read `ARGUS_SKILL_PHYSICS_TARGET_PAPER_TYPE` (default `auto`; set `original_research_article`) and
`ARGUS_SKILL_PHYSICS_ALLOW_DOWNGRADE` (default `true`; set `false`). When
`TARGET=original_research_article` and `ALLOW_DOWNGRADE=false`:
- diagnostic benchmark / reproduction are **intermediate only, not a success terminal**;
- Paper-Type gate emits a blocker if paper_type is a downgrade AND no `ORIGINAL_RESEARCH_NO_GO.md`
  with a completed ≤2-round pivot record exists;
- manuscript hard gate refuses `project_done` for a downgrade terminal in this mode.

### 5. Anti-over-hedging / idea-centric manuscript rule (`manuscript.py` + banner) — issue 六
Add a paper-contract check: the same boundary/disclaimer (`not a new phase`, `not universal scaling`,
`no disorder`, `no materials`, `no interactions`, `not a new bulk-edge theorem`) may appear **at most
twice** across the main text; require a **single central thesis + one stated non-trivial insight**;
push scope/boundary into Results/Limitations, not Abstract/every section. Mirror upstream
`idea_centrality_and_insight` (commit 81535928). Banner tells the agent: state boundaries once/twice,
spend the space on the **physical meaning** of what WAS done.

### 6. Progress-aware watchdog (harness + env)
Phase B launch templates use **no `timeout`**. Recommend `ARGUS_SKILL_DAEMON_IDLE_EXIT_MIN=0` for
long original-research missions. External monitor polls files/stage/codex/repair; writes
`STALL_REPORT.md` + requests confirmation ONLY on real no-progress. Never touch external missions.

## Test plan (Phase A5 smoke)
manuscript repair loop; V3 registry 90/90/14; stage-entry contract injected; NSL artifact schema
verifier; original-research mode rejects diagnostic-benchmark `project_done`; capability consumption
trace records real usage; anti-over-hedging check fires on repeated disclaimers; release tests.

## Out of scope this round
Full per-capability execution for all 194 caps (start with the trace + numerical/theory binding);
integrating the deferred planner-idle fixes (`06691811/a5f9cd95/1a9b2cf6`) — tracked separately.
