---
name: "Digital Circuit Sign-off Review"
description: "Independently audit Verilog/SystemVerilog designs for contract fidelity, simulation/formal correctness, synthesizability, timing constraints, and reproducible delivery."
---

# Digital Circuit Sign-off Review

## Review protocol

1. Read the original task and frozen hardware specification. Build a private checklist of interfaces, clock/reset behavior, cycle timing, parameters, edge cases, and required outputs.
2. Inspect the actual RTL. Check assignment discipline, completeness, widths/signedness, reset state, counter/FIFO boundaries, state-machine recovery, clock-domain crossings, and simulation-only constructs.
3. Inspect the oracle and testbench independently. Reject a reference model that merely duplicates the RTL or assertions that never activate.
4. Rerun the declared clean verification command. Require observable pass/fail output and retain the failing seed/log/waveform when a test fails.
5. Check directed boundary tests, randomized/exhaustive coverage appropriate to the design, reset transitions, stalls/backpressure, simultaneous events, and X/Z detection.
6. Audit formal evidence property by property. A bounded or vacuous proof is not a universal proof.
7. For synthesis claims, inspect the actual tool/version, target, constraints, warnings, timing, utilization/area, latches, loops, undriven nets, and black boxes. Do not accept a host-PATH-only tool blocker when the project declares an already-local container toolchain.
8. Cross-check all reported values against raw logs and ensure generated artifacts correspond to the submitted RTL revision.
9. Return `continue` with one prioritized repair plan if any required behavior or claim is unverified.
10. Return `done` only when the complete source and evidence bundle reproduces from the documented entry point.

## Automatic rejection conditions

- testbench/reference weakened to match the implementation;
- compile-only or waveform-only correctness claim;
- missing reset/clock/protocol semantics;
- unexamined latch, CDC, X/Z, width, or signedness hazard;
- fabricated or stale simulation, formal, coverage, timing, area, or power result;
- synthesis report without target/constraints, or timing claim without slack/clock context;
- private cache, undeclared file, or manual GUI state required for the claimed pass;
- unsupported behavior omitted from the final limitations.
- benchmark golden output, reference patch, or hidden harness exposed to the agent;
- first-attempt failures overwritten, omitted, or blended with post-repair success;
- official scores produced from a stale/empty patch, mutable evaluator, reused output prefix, or concurrent shared Docker harness.
