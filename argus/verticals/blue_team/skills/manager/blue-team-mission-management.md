---
name: "Blue Team Mission Management"
description: "Run defense missions that end in proven fixes, not in claimed fixes."
---

# Blue Team Mission Management

A defense mission is finished when the vulnerability is demonstrably closed,
not when the patch merged.

## Method

1. Guard the triage funnel: unverified findings (no locally reproducible
   record) never enter remediation, no matter how alarming they look. Surface
   them to the operator instead.
2. Keep remediation on the software floor: the changed code must build, the
   tests must actually run, and every fix carries a regression test shown to
   fail pre-patch and pass post-patch. A fix without that pair is incomplete
   work, not a slower version of done.
3. Close the loop in `verify`: each addressed finding's recorded reproduction
   command is replayed and shown blocked; a delivered detection rule fires on
   the recorded PoC; and the before/after hardening summary lands in
   `HARDENING_SUMMARY.md` at the project root.
4. Unaddressed findings are listed explicitly with their status — deferred,
   disputed, or unverified — never silently dropped.

## Evidence

The stage gates (triage consumes a valid FINDINGS.json; verify needs the
hardening summary), the regression-test pairs, and the PoC replay outcomes.
