---
name: "Red Team Mission Management"
description: "Keep an offensive mission inside its declared engagement and ship only evidence-bound findings."
---

# Red Team Mission Management

The engagement declaration is the boundary of the mission. It is not a
formality the pipeline can skip.

## Method

1. Treat `authorize` as a hard gate: `SECURITY_ENGAGEMENT.json` must parse,
   the window must be current, every mission target must be covered, and the
   impact flags must match the requested methods. The stage refuses to close
   otherwise — loudly — and no Manager decision folds that away.
2. Keep every subsequent stage inside the declared scope. If mid-mission work
   discovers a target or method the declaration does not cover, stop and ask
   the operator to amend the declaration; do not proceed on verbal scope.
3. Enforce the evidence floor: a vulnerability claim without a reproduction
   artifact (PoC script plus raw tool output, hash-bound in `FINDINGS.json`)
   is `unverified`, not a finding. No recall-only or scanner-only claims.
4. Calibrate severity honestly: P0 unauthenticated RCE / auth bypass on a
   default deployment; P1 authenticated RCE, unauthenticated memory-safety
   violation, DoS-by-default within the declared impact flags, or default
   sensitive-data exposure. Report a null result as a null result.

## Evidence

The declaration gate output per stage, the finding count with severities, and
the unverified list with reasons.
