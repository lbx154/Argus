---
name: "Defense Triage Planning"
description: "Plan remediation from a triaged, reproducible findings queue — never from raw scanner output."
---

# Defense Triage Planning

Plan defense work from evidence, not from alarm count.

## Method

1. Consume `FINDINGS.json` at the project root. An external scanner export is
   first mapped into the same shape (id, target, severity P0–P4, title,
   evidence artifacts with hashes, reproduction command) — nothing unmapped is
   planned.
2. Triage before planning work: collapse duplicates (same id, or same target
   and title), rank by severity, and drop every finding whose record is not
   locally reproducible (missing PoC, missing raw output, broken sha256
   binding, no reproduction command) to `unverified` with its reason. Dropped
   findings never enter remediation; they stay visible for the operator.
3. Plan one remediation task per actionable finding, ordered by severity,
   against the finding's declared target. Each task names the finding id, the
   reproduction it must defeat, and the regression test that will prove the
   fix.
4. Plan `verify` as part of each fix, not as an afterthought: the PoC replay,
   any detection rule, and the hardening summary entry.

## Evidence

The triage result (actionable, unverified with reasons, collapsed duplicates)
and the per-finding remediation plan it was derived from.
