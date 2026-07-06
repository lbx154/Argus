---
name: Repair Governance Snapshot Verifier Drift
description: A playbook for fixing brittle governance/audit verifiers when historical setup-era evidence must remain enforced, but current manifest or checksum expansions make exact output comparisons fail spuriously.
category: governance-verification
version: 1
created_at: 2026-07-05T21:50:27.170451+00:00
task_history:
  - "Read `AGENTS.md`, `research/GROUND_TRUTH.md`, `research/RESEARCH_GATE_AUDIT.md`, `research/CHECKLISTS.json`, `setup/SETUP_STAGE_AUDIT.{md,json}`, and `setup/verify_setup_audit_consistency.py`. Reprodu"
---

# Repair Governance Snapshot Verifier Drift
## Description
A playbook for fixing brittle governance/audit verifiers when historical setup-era evidence must remain enforced, but current manifest or checksum expansions make exact output comparisons fail spuriously.

## Category
governance-verification

## When to use
- A verifier compares recorded audit output to current command output too strictly.
- Historical setup/audit evidence must still be checked after legitimate post-setup files were added.
- A checksum or manifest baseline needs to include an accepted governance repair.
- The fix must prove it did not alter benchmark definitions, scorers, timing, correctness, candidates, harnesses, or GPU-dependent behavior.

## When NOT to use
- The failure comes from actual corrupted evidence or changed benchmark semantics.
- The task asks to modify scorer logic, benchmark cases, candidate code, or performance behavior.
- The verifier should intentionally require byte-for-byte transcript equality.
- The repository lacks any stable baseline command, manifest, or audit artifact to validate against.

## How to solve
1. Read the governing instructions and evidence files: `<agent_instructions>`, `<ground_truth_doc>`, `<audit_report>`, `<checklists_json>`, `<setup_audit_md>`, `<setup_audit_json>`, and `<governance_verifier>`.
2. Reproduce the failure with `<verifier_command>`, saving the relevant error text and identifying whether the mismatch is from added current manifest entries, removed evidence, command ordering, path changes, or real checksum failure.
3. Run the baseline command directly, usually `<checksum_command>`, and confirm whether the current accepted manifest itself passes.
4. Identify the intended invariant:
   - Current baseline manifest must validate successfully.
   - Setup-era recorded `OK` evidence lines must still be represented in current validation output.
   - New post-setup manifest entries should be allowed when they are valid and intentionally accepted.
5. Change `<governance_verifier>` so it validates behavior rather than exact stdout equality:
   - Execute `<checksum_command>` from the correct repository root.
   - Fail if the command exits nonzero.
   - Parse recorded setup-audit success lines from `<setup_audit_md>` and/or `<setup_audit_json>`.
   - Parse current success lines from the checksum command output.
   - Require every recorded setup-era success line or normalized equivalent to exist in current output.
   - Do not require current output to contain only the setup-era lines.
6. Keep normalization conservative:
   - Normalize path prefixes only if the existing verifier or audit format already does so.
   - Preserve filename and status semantics.
   - Treat missing recorded evidence as failure, not as an empty success set.
7. Update the accepted baseline manifest `<baseline_manifest>` only for intentional governance artifacts introduced by the repair, such as `<repair_note>` or edited audit text.
8. Write `<repair_note>` documenting:
   - Root cause of the verifier failure.
   - Code change made to the verifier.
   - Why the change is governance-only.
   - Explicit statement that no GPU runs, harness behavior, benchmark scorer edits, benchmark definitions, timing logic, correctness logic, or candidate implementations were changed.
9. Update audit/checklist text only where needed to reflect the verifier repair and accepted snapshot. Avoid changing benchmark-facing claims unless independently verified.
10. Run acceptance commands:
   - `<verifier_command>` exits 0.
   - `<checksum_command>` exits 0.
   - `<clean_status_command>` prints empty stdout, using the task’s requested untracked-file policy.
11. Inspect the final diff and confirm it is limited to governance verifier, audit/checklist text, baseline manifest, and repair documentation.

## Pitfalls
- Replacing exact stdout equality with no check at all; the setup-era evidence must remain enforced.
- Updating the checksum baseline to bless drift before proving the current manifest validates.
- Letting new valid manifest entries hide missing historical `OK` lines.
- Accidentally editing scorer, harness, benchmark definitions, candidate code, timing, or correctness logic.
- Depending on GPU, benchmark harness execution, or performance measurements for a governance-only verifier repair.
- Ignoring command working directory; checksum manifests are often path-sensitive.
- Over-normalizing paths or statuses until distinct evidence lines collapse into false matches.