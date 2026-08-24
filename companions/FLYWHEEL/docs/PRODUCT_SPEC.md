# Argus Research Data Flywheel — Product contract

## Product job

Argus Research Data Flywheel is a control plane for one researcher, the Argus team, or another
small lab operating multiple autonomous research campaigns against real conference
deadlines. It does not assume that the same ideas fit every operator: a TeamProfile and
immutable condition snapshot bind actual expertise, methods, data permissions,
resources, time, goals and policy to each ideation run. In ten seconds the operator
should be able to answer:

1. Which venue or Campaign needs attention now?
2. Is Argus merely alive, or is it producing inspectable progress?
3. What is the current idea, claim, experiment and manuscript state?
4. What evidence supports or contradicts each material claim?
5. Which action is safe to take next, and which action needs human approval?
6. Under which team conditions was a candidate generated and how did humans label it?

It is not a paper generator and it does not claim that a paper will be accepted or
receive an oral. `oral` is an explicit aspiration used to raise the evidence and review
bar. It is never a completion certificate.

## Product boundaries

- The control-plane source is packaged under repository-root `companions/FLYWHEEL`,
  while its state, Campaign workspaces and staged releases remain companion-owned and
  isolated from Argus package files and project worktrees.
- The implemented launch path connects through an authenticated Argus WebAPI. The local
  CLI adapter currently returns a dry-run argv only; execution is a separate,
  unimplemented/externally approved step rather than a second automatic launch path.
- WebAPI `CreateDaemonIn` cannot select Pi/Copilot/etc. per launch. Role backends are
  preconfigured on the target Argus instance; Flywheel persists the target's reported
  connection identity and live project snapshots rather than asserting an override.
- Running Campaigns freeze the Prompt revision and verified Argus release truth. If no
  verified full SHA is available the manifest records `release_pinned=false`; a config
  string or telemetry fragment is only a reference, not a silently inferred pin.
- A dirty or running Argus checkout is never updated in place. Explicit release staging
  writes only an exact verified SHA beneath Flywheel's own content-addressed release
  directory; tests, canary and adoption remain separate gates.
- External submission, publication, authorship, ethics approval and disclosure remain
  human actions.
- Dataset export is consent-, license- and redaction-gated. Export never starts model
  training, uploads records, or promotes a Campaign.
- TeamProfile is a portable conditioning workspace, not an authenticated tenant. The
  current API has no user auth, RBAC, row-level tenant policy or tenant secret isolation;
  mutually untrusted teams require separate deployments/databases/runtime roots.
- Negative, refuted and inconclusive scientific results are valid outcomes. Red UI
  states are reserved for operational or integrity failures.
- Secrets stay server-side and are redacted from API responses, events and logs.

## Primary workflows

The interface is an independent Argus side product. It reuses Argus design lineage—its
brand mark, blue primary accent, Geist/Chinese font stack, compact bordered workbench,
role/status semantics and restrained radii—while adding Flywheel's Evidence Horizon,
condition snapshot, annotation and outcome surfaces. Visual resemblance never implies
that demo data, a Flywheel release reference or local state came from a live Argus host.

### Team intake and conditioned ideation

The operator first creates a TeamProfile containing real expertise, executable methods,
authorized data/testbeds, constraints, goals and policy. `POST /api/ideation/runs`
combines that profile with one venue/deadline, optional resource and an operator
completion target. Flywheel freezes canonical `CONDITION_SNAPSHOT.json` and
`IDEATION_OBJECTIVE.md` under a content-addressed path. Condition schema v3 preserves
the operator's original team-condition statement and binds
all-or-neither source context as reference SHA-256 + content SHA-256 (not the raw ref),
fresh-discovery state and preflight attestations; the run ledger separately binds an
optional connection and source provenance. The Objective carries the JSON-encoded raw
reference only for runtime location, verifies content bytes or blocks, and requires a
fresh frozen source snapshot when none was supplied.
Changing the team or its conditions creates a new objective; an existing run never
silently inherits later profile edits.

The objective asks for Builder and Breaker tracks followed by an Arbiter and independent
fresh-context reviews. It preserves ties, vetoes and `NO_WINNER` rather than optimizing
one composite score. The optional Campaign is created idle; run creation itself does
not contact or start Argus.

### Venue horizon

The platform imports Full/Regular Paper deadlines, preserves the official/forecast
distinction, shows forecast intervals, and schedules from the earliest plausible date.
The seeded planning universe is 58 CCF-A venues and 85 deadline events in the inclusive
2026-08-22..2027-08-22 window: 28 `official_confirmed` and 57 `forecast`. This is a
declared universe, not all conferences worldwide; forecast values require official-page
reconfirmation before lock or submission.
Real start requires an offset-qualified wall-clock ceiling: no later than the official
date for confirmed targets or the forecast interval start for forecasts.
The primary research horizon starts around D-180. D-30 is a hostile-review and
convergence gate, not the default point for starting research from zero.

### Idea radar

Each registered venue has five versioned seed ideas so all 58 venues remain browsable
and testable. Those 290 seeds are coverage probes and cold-start material, not a
personalized answer set or novelty evidence. Source adapters refresh evidence from
arXiv, OpenReview and GitHub, then record what changed since the prior snapshot.
Production candidates are generated under a TeamProfile condition snapshot. Refreshes
can lower novelty confidence; they cannot silently rewrite a locked Campaign or run.

### Campaign launch

The operator reviews the compiled Prompt, chosen Argus connection, backend configuration
reported by that target, resources, budget and target venue. Before confirmatory work,
`POST /api/campaigns/{id}/locked-contract` freezes the human-approved claim, metric,
effect, split, seeds and baselines but contacts no Argus and triggers neither launch nor
submission. A never-launched idle Campaign versions in place; a prior-launched
non-active Portfolio promotes to a new locked child; an active Campaign is rejected.

Starting is separate: `POST /api/campaigns/{id}/start` requires
`human_approved=true`, a nonblank reason and an actor. It authenticates the frozen
packet, verifies connection/release/resource/concurrency/time/preflight gates, freezes
the approval in the launch manifest, and dispatches a new Argus project. Retry must
reuse the exact approval and idempotency receipt. Local connections use a Flywheel-owned
isolated workspace; remote connections leave `workdir` and `launch_cwd` empty so the
target Argus instance allocates its own local workspace rather than receiving a
meaningless Flywheel-host path.

### Campaign supervision

The platform projects Argus snapshots and events into orthogonal state dimensions:

- schedule: dormant / due / admitted / deferred / expired
- execution: queued / running / paused / blocked / terminal
- science: exploring / hypothesis_locked / untested / inconclusive / supported /
  refuted / claim_scoped
- review: none / pending / continue / replan / certified
- integrity: unknown / checking / pass / warn / fail / quarantined
- human gate: currently projected through `schedule_state=awaiting_approval` and
  approval events/actions rather than a separate database state column
- deadline: on_track / at_risk / critical / missed

Process liveness and evidence progress are separate signals.

### Independent Viewer

Viewer runs as a separate OS process and clean context. If its adapter uses Argus, that
adapter must create a separate project/backend rather than reusing the Campaign. The
numeric protocol scores novelty, significance, technical quality, empirical rigor,
clarity, reproducibility and venue fit; evidence citations and blockers carry baseline,
ethics/limitations and integrity findings. Scores are simulated peer review, not
official venue scores or acceptance probabilities.

Before queueing, Flywheel reads only allowlisted, bounded artifact previews through the
target Argus API and freezes a content-addressed `EVIDENCE_SNAPSHOT.json`; arbitrary
client-local paths are not trusted. No eligible evidence means `score: null`.

### Human labeling and dataset export

Imported personalized candidates are immutable within an ideation run. Human labelers
can add dimension-level 0–10/`null` labels plus a shortlist/revise/reject/abstain
decision, or compare two candidates with left/right/tie/abstain preference. The system
retains rationales and disagreement rather than treating an average as ground truth.

The JSONL exporter includes only records that pass explicit consent, nonblank license
basis and redaction/pseudonymization gates. It assigns all examples from one ideation
run or source Campaign to the same deterministic train/validation/test split. The
exporter does no training; a separate approved data-governance and training pipeline is
required.

### Post-submission outcomes and rebuttal

Operators can record a paper version, pseudonymous reviewer feedback, numeric score or
label, confidence, questions and decision. These are human-entered records, not a
submission connector. An explicitly authorized follow-up freezes a rebuttal objective
and creates a new idle `rebuttal_follow_up` Campaign; it does not start Argus, contact
reviewers or submit a response. Outcome reviews may enter JSONL only when the operator
confirms training-export consent, review-use rights and redaction.

## Research state machine

```text
TEAM_PROFILE -> CONDITION_FROZEN -> PROMPT_READY -> HORIZON_SCAN -> PORTFOLIO -> PILOT
  -> NO_WINNER | DEFERRED | WINNER_PROPOSED
  -> WAITING_HUMAN_LOCK
  -> RESEARCH -> WRITE -> INTEGRITY_PRE
  -> REVIEW -> REVISE -> RE_REVIEW -> optional RE_REVISE
  -> INTEGRITY_FINAL -> FINALIZE -> PROCESS_SUMMARY
  -> SUBMISSION_READY -> WAITING_HUMAN_SUBMIT -> HUMAN_RECORDED_OUTCOME
  -> optional IDLE_REBUTTAL_FOLLOW_UP -> ARCHIVED
```

Both integrity gates are blocking. The final integrity pass starts from scratch and
must have zero unresolved issues. Review/revision is bounded to prevent endless score
optimization.

## Definition of done

- The UI renders the complete seeded venue horizon and all 290 baseline idea seeds.
- The 290 baseline seeds mean 58 venues × 5 and are browsable through generated
  `CATALOG.md`; they use each venue's earliest planning target, are not 85 × 5, and are
  never represented as TeamProfile-personalized recommendations.
- A TeamProfile-conditioned run freezes a canonical condition snapshot and objective,
  and can create only an idle Campaign until a separate strict Start approval.
- Candidate imports are immutable per run; scalar labels and pairwise preferences keep
  provenance, consent, license and redaction state.
- Local and remote Argus WebAPI connections can be tested without exposing tokens.
- A Campaign can compile and preview a content-addressed structured Prompt.
- Starting a Campaign dispatches a real adapter or returns a precise actionable error;
  it never reports synthetic success.
- Live event projection distinguishes `alive` from `making_progress`.
- Viewer is demonstrably a separate process; it authenticates a bounded allowlisted
  evidence snapshot, and its report is stored with provenance.
- Source refreshes are cached, rate-limit aware and produce a before/after delta.
- Every claim-critical artifact intended for promotion is traceable to its Prompt,
  code, data and run revision; operators must verify stored hashes because local
  filesystem permissions do not make files cryptographically immutable.
- Desktop and mobile approval paths are usable with keyboard focus and reduced motion.
- Outcome/rebuttal records can be captured without auto-submission, and gated JSONL uses
  group-safe splits while declaring that automatic training is false.
- Tests cover seed import, Prompt invariants, connection redaction, idempotent launch,
  immutable Locked Contract versioning/promotion, wall-clock admission, state
  transitions, source parsing and Viewer scoring/provenance.
