# Requirement traceability

This file is the completion checklist. A checked item requires executable or rendered
evidence; implementation intent alone is insufficient.

The public product intent and normalized operating contract are in
[`PRODUCT_SPEC.md`](PRODUCT_SPEC.md). Private task transcripts and operator machine
fingerprints are deliberately excluded from the release. `scripts/check.ps1` closes
only automated evidence; the real-Argus, current-venue, rendered-UI and human gates in
[`ACCEPTANCE.md`](ACCEPTANCE.md) remain mandatory.

| ID | Requirement | Evidence required |
|---|---|---|
| R0 | Preserve the public product intent without publishing private task transcripts or operator machine fingerprints | `PRODUCT_SPEC.md` contract + release content audit |
| R1 | Independent folder; no modifications to Argus/argus-skill | workspace diff/file audit |
| R2 | Calendar for all supplied deadline targets, official vs forecast, reminders | seed-count test + calendar UI |
| R3 | Venue seed previews, TeamProfile-conditioned ideation, explicit Locked Contract freeze/promotion, and separate Campaign Start | condition/lifecycle/hash tests + UI interaction |
| R4 | Local and server Argus connections with secret redaction | mocked WebAPI contract tests |
| R5 | Real-time Argus activity, progress, artifacts and feedback | polling/event projection test + campaign UI |
| R6 | Structured resource-aware prompt with oral aspiration and integrity boundaries | prompt invariant tests + preview UI |
| R7 | Pi/Copilot/Codex/etc. remain target-Argus-side execution backends; WebAPI does not pretend to apply a per-launch override, Flywheel stores actual reported snapshots, and Viewer stays independent | connection protocol/snapshot + backend-override rejection tests |
| R8 | Independent Viewer OS process with venue rubric and provenance | worker process integration test |
| R9 | arXiv/OpenReview/GitHub refresh with idea delta | parser/cache/delta tests + Radar UI |
| R10 | User-configurable GPU/API/compute resources | persistence/API test + settings UI |
| R11 | Argus-derived brand/color/type/workbench language in a polished, responsive, accessible independent Flywheel UI | production build + rendered UI audit against upstream design lineage |
| R12 | Mandatory research integrity/review checkpoints and human approval | state/gate tests + approval UI |
| R13 | Safe pause/drain; no in-place live Argus update | adapter tests + documented invariant |
| R14 | Install/run documentation and reproducible local startup | clean install + smoke test |
| R15 | Export one resource/domain/venue-aware baseline Prompt Packet for every one of the 290 = 58 × 5 seeds, bound to each venue's earliest target rather than 85 × 5 rounds; mark `launch_ready=false` and never present these as TeamProfile-personalized outputs | exporter temporary-directory test + `CATALOG.json` distribution + browsable `CATALOG.md` links |
| R16 | Operational truth boundaries for security, reminders, backup, launch reconciliation and release handling | `OPERATIONS.md` + manual incident drill |
| R17 | Freeze Locked vN without launch/submission; promote a prior-launched non-active Portfolio to an immutable child; reject active promotion; start from authenticated frozen files | Locked Contract idempotency/version/lineage/tamper/start tests |
| R18 | Require offset-qualified wall-clock admission and use official date or conservative forecast-window start | missing/naive/late cutoff rejection tests + manifest assertions |
| R19 | Record target Argus protocol/runtime truth and treat repository observations as point-in-time evidence rather than permanent latest/stable claims | connection Test persistence assertions + read-only audit record |
| R20 | Portable TeamProfile conditions: expertise, methods, data access, constraints, goals and policy; each ideation run freezes schema-v2 source context/preflight plus an immutable canonical snapshot/objective | workflow API tests comparing condition/objective hashes, all-or-neither source digest verification and content-addressed files |
| R21 | Builder/Breaker/Arbiter conditioned ideation and independent multi-review without collapsing disagreement or fabricating missing scores | Objective invariants + multiple Viewer requests/reviewer score projection |
| R22 | Human scalar candidate labels and pairwise preferences with provenance and immutable candidate import | schema/range/uniqueness/immutability API tests |
| R23 | Consent/license/redaction-gated JSONL with run/campaign group-safe splits and no automatic training | export eligibility/schema/split/header tests + human data-governance audit |
| R24 | Record pseudonymous post-submission outcomes and create an idempotent idle rebuttal follow-up without auto-Start or submission | outcome/rebuttal API tests + UI flow + human submission boundary |
| R25 | Viewer uses allowlisted, bounded, content-addressed Argus evidence snapshots; empty/tampered evidence never receives a fabricated score | evidence snapshot, artifact-client and queue claim/recovery tests |
| R26 | Strict Start approval, truthful release pin/origin and target-owned remote workspace semantics | launch manifest/retry/resource/concurrency/release/workspace tests |
| R27 | TeamProfile is explicitly not multi-tenant authorization; untrusted teams require isolated deployment/database/runtime/credentials | security documentation + deployment audit |
| R28 | Default release monitoring distinguishes official `microsoft/ArgusAgent` from explicitly selected `lbx154/Argus` preview and never equates different SHAs | origin/ref/SHA registry and staging evidence |
