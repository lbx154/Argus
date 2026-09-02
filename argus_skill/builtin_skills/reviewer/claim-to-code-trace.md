---
name: "Claim-to-Code Trace"
description: "Check whether the code path that produced a paper result implements its named mechanism, formula, and comparison."
---

# Claim-to-Code Trace

Use this in Build alignment review or the final scientific Review. Read the
selected thesis or manuscript, the actual run command and configuration, and the
reachable training or evaluation path. Do not create a separate trace report.

For each load-bearing claim:

1. Rewrite it as concrete inputs, transformation, output, scope, and timing.
2. Locate the entry point that produced the cited result.
3. Follow the real call chain with the executed configuration and branches.
4. Compare formulas symbol by symbol: operands, signs, masks, reductions,
   normalization, gradient boundaries, sampling, and where the quantity enters
   the loss or metric.
5. Use existing runtime output or the smallest authorized instrumentation check
   to confirm the branch ran.

Return exactly one conclusion:

- `MATCHES`: the executed path computes the claimed mechanism and comparison;
- `CONTRADICTS`: the path runs but computes something materially different;
- `NOT_IMPLEMENTED`: the mechanism is absent or unreachable.

Function names, comments, dead helpers, or isolated unit tests do not prove
execution. For a central `CONTRADICTS` or `NOT_IMPLEMENTED` result, either fix
the implementation and regenerate affected evidence or rewrite the paper to the
method that actually ran. Report the concrete code locations and affected paper
claims through the normal Reviewer response.
