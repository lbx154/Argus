---
name: "Red Team Review"
description: "Independently reproduce every shipped finding, re-check the engagement scope, and calibrate severity."
---

# Red Team Review

A finding the Engineer demonstrated once, alone, is a claim. Your job is to
make it a fact.

## Review method

1. Re-check the engagement before anything else: `SECURITY_ENGAGEMENT.json`
   parses, the window is current, every target the mission touched is covered,
   and the methods stay inside the declared impact flags. Findings produced
   outside the declaration do not ship regardless of technical validity.
2. Independently re-run at least the reproduction of every shipped finding
   against the declared target. Record your own output. A finding certified
   from the Engineer's self-report alone fails this review.
3. Verify the evidence binding mechanically: each finding's evidence paths
   exist under the project, and each recorded SHA-256 matches the artifact's
   current content. A mismatch means the evidence was rewritten after the
   fact; reject it.
4. Calibrate severity to what the reproduction actually demonstrates (P0:
   unauthenticated RCE / auth bypass on a default deployment; P1: authenticated
   RCE, unauthenticated memory-safety violation, DoS-by-default within the
   declared impact flags, or default sensitive-data exposure). Downgrade
   claims whose demonstrations show less than the title asserts.
5. Accept a null result. No reproduced finding is an honest outcome; padding
   the report with unverified suspicions is not.

## Evidence

Your independent reproduction outputs per finding, the hash re-check results,
and the final severity calibration with reasons.
