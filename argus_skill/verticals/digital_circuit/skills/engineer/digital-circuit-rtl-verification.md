---
name: "Digital Circuit RTL and Verification"
description: "Design synthesizable Verilog/SystemVerilog from an explicit cycle-level contract and prove it with reproducible simulation, assertions/formal checks, and synthesis evidence."
---

# Digital Circuit RTL and Verification

## Operating method

1. Read the existing repository instructions and tool scripts before selecting a simulator, formal engine, or synthesis flow.
2. Freeze a hardware contract before RTL edits:
   - ports, widths, signedness, parameters, and legal ranges;
   - clock domains and reset polarity, synchrony, values, and release behavior;
   - cycle-level protocol timing, backpressure, latency, and throughput;
   - behavior for stalls, boundaries, overflow, illegal inputs, and recovery.
3. Inspect the available tools instead of assuming them. Prefer project-native commands; then inspect host `PATH`, declared project environments, and already-local container images before reporting a blocker. Never pull an undeclared image or access the network merely to obtain a tool. Record exact human-readable versions and image identities; serialize shared Docker/EDA resources with the project lock.
4. Keep synthesizable RTL separate from testbench, formal, generated, and report artifacts.
5. Use `always_comb`/complete combinational assignments and `always_ff`/nonblocking sequential assignments where SystemVerilog is available. Make width casts and signedness explicit.
6. Build an independent verification oracle. Do not copy the RTL algorithm into the scoreboard and call agreement proof.
7. Verify exact expression width/signing, combinational current-input versus
   sequential prior-state behavior, reset polarity/synchronicity, documented
   latency, first/last values, counter and FIFO boundaries, stalls/backpressure,
   simultaneous events, parameter extremes, initialization uncertainty, and X/Z
   behavior. Exhaust public small spaces; otherwise add public-contract
   metamorphic properties. Preserve random seeds.
8. Add assertions for protocol and state invariants. Use formal proof when the state space and toolchain support it, but state exactly which properties were proved.
9. For synthesizable designs, run lint and synthesis with explicit target and constraints. Audit latches, loops, undriven nets, black boxes, critical warnings, timing, and utilization/area. If a frozen functional benchmark does not score synthesis or PPA, write `synthesis/NOT_APPLICABLE.md` with the exact scorer-scope reason instead of adding unscored work.
10. Produce one clean reproduction command and a results summary that links every claim to a raw log/report.

## Non-negotiable rules

- Do not modify expected values, assertions, or the reference model merely to make RTL pass.
- Compile/elaboration success is not functional correctness.
- A waveform screenshot is diagnostic evidence, not a substitute for a self-checking result.
- Do not report coverage, timing, frequency, area, power, or formal proof unless a real tool emitted it.
- Do not use unsynthesizable delays, force/release, or testbench-only constructs in claimed synthesizable RTL.
- Do not hide warnings; classify and justify every waiver that affects correctness or implementation.
- If required EDA tooling or a target library is unavailable after host, project-local, and declared local-container discovery, preserve the RTL and verification evidence and return a bounded blocker or `synthesis/NOT_APPLICABLE.md`.

## Expected artifacts

```text
design/SPEC.md
rtl/ or src/
tb/ or testbench/
formal/                    # when applicable
verification/RESULTS.md
synthesis/REPORT.md        # or synthesis/NOT_APPLICABLE.md
RESULTS.md or DELIVERY.md
Makefile / justfile / scripts/verify.sh
```
