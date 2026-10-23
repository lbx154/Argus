---
name: "Blue Team Review"
description: "Independently verify each fix: PoC replay blocked, regression evidence genuine, hardening summary complete."
---

# Blue Team Review

The Engineer's patch is a claim that the vulnerability is closed. Your job is
to replay the attack and find out.

## Review method

1. Triage integrity first: confirm unverified findings stayed out of
   remediation and that the actionable queue matches the validated
   FINDINGS.json records (hashes included).
2. Re-run each addressed finding's recorded reproduction command against the
   fixed target yourself. Record your own output. The exploit must fail
   cleanly; a fix certified from the Engineer's self-report alone fails this
   review.
3. Verify the regression evidence is genuine: the test exists, its expectation
   is derived from the finding (not copied from the patch), and it
   demonstrably fails on the pre-patch code while passing after. Run the
   build and the tests yourself for the touched path.
4. Check the fix closes the class, not the demonstration: trace the changed
   boundary through unchanged callers and probe a neighboring input the PoC
   did not use.
5. If a detection rule shipped, run it against the recorded PoC and confirm it
   fires on the pre-fix behavior; confirm findings without a rule say so
   explicitly.
6. Confirm `HARDENING_SUMMARY.md` records the before/after comparison for
   every addressed finding — reproduction before, change, replay after,
   detection result — with nothing silently dropped.

## Evidence

Your independent PoC replay outputs, the regression-test run results, and the
hardening summary contents you verified.
