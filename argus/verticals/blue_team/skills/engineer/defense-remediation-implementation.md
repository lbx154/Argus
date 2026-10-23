---
name: "Defense Remediation Implementation"
description: "Fix confirmed vulnerabilities with the smallest real change and fail-before/pass-after regression evidence."
---

# Defense Remediation Implementation

Fix the vulnerability, not the symptom the scanner reported, and prove the fix
with a test that used to fail.

## Method

1. Start from the finding's record: read the PoC and the raw output, then run
   the reproduction command yourself to see the vulnerability in the current
   code. If you cannot reproduce it, stop — the finding belongs back in
   triage as unverified, not in your patch.
2. Fix the vulnerability class at its root — validate the input at the
   boundary, use the safe API, correct the flawed control — rather than
   blocking the one PoC string. The smallest change that closes the class
   beats a filter that closes the demonstration.
3. Write a regression test derived from the finding's reproduction (the PoC
   or the recorded failing behavior), never from the patch's own behavior.
   Record it failing against the pre-patch code, then passing after the fix.
   A test you cannot show failing first is not regression evidence.
4. Run the project's own build and tests for the touched path — the changed
   code must compile and the tests must actually execute and pass.
5. If you deliver a detection rule (signature, alert, log check), run it
   against the recorded PoC and show it fires on the pre-fix behavior.

## Evidence

Per fix: the reproduction output before, the regression test with its
pre-patch failure and post-patch pass, and the build/test results.
