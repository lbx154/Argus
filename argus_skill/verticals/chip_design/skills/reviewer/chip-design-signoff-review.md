---
name: Chip Design Sign-Off Review
description: Independently review digital chip and accelerator projects for workload and architecture closure, EDA/PDK/IP readiness, RTL correctness, verification independence, PPA integrity, prototype evidence, benchmark fairness, claim scope, and tapeout readiness.
category: chip-design-review
priority: high
version: 1
created_at: 2026-07-20T00:00:00+00:00
---

# Chip Design Sign-Off Review

Review raw artifacts and rerun decisive commands only when material evidence is
missing, stale, contradictory, implausible, or not reproducible from the recorded
command. Never certify from the Engineer summary alone. A successful canonical
PPA packet that binds the current RTL, verification, constraints, target library,
toolchain, and raw log hashes is itself the decisive same-hash run: inspect those
artifacts and their consistency, but do not launch a second full Yosys/ABC PPA
solely for ceremony. Rerun PPA only after a source/constraint/toolchain binding
changes or when the canonical evidence is incomplete or suspect.

## Delivery-level gate

Read `design/CHIP_SCOPE.json` first. Keep these claims distinct:

- `rtl_ip`: synthesizable, verified IP with synthesis/PPA evidence;
- `fpga`: implemented and measured on named hardware;
- `gds`: open/commercial PDK physical design with declared closure;
- `pre_tapeout`: all computer-executable digital implementation and sign-off checks
  are closed, but no foundry submission or fabrication is claimed;
- `tapeout`: foundry/package/IO and independent sign-off readiness;
- fabricated silicon: requires measured physical chips and is outside this enum.

Reject language that promotes one level into another.

## Definition and architecture

Require a frozen workload, quality/numerical formats, interfaces, target, baselines,
non-goals, and measurable acceptance criteria. Challenge arithmetic-intensity,
bandwidth, SRAM, DMA, host, KV-cache, latency, throughput, and power assumptions.

For batch-1 LLM decode, reject raw TOPS as the sole design target. Verify bytes/token,
bandwidth efficiency, utilization, and host offload. Check Amdahl leverage before
accepting a large architectural change.

## Hard environment and IP gate

Fail/continue when:

- required capabilities in `ENVIRONMENT_AUDIT.json` are not ready;
- tests and PPA use incompatible environments without justification;
- the chosen PDK, FPGA, standard cells, SRAM, IP, or compiler lacks a pinned revision;
- licenses or source provenance are missing;
- an agent reinvents a mature project/vendor component without evidence;
- a toolchain failure is incorrectly used to refute an architecture.

Inspect `TOOLCHAIN_CANDIDATES.md`, `IP_REUSE_PLAN.md`, `TARGET.json`, containers,
project scripts, and registry queries.

## RTL review

Audit:

- manifest/source/generator consistency;
- widths, signedness, truncation, saturation, overflow, and numerical policy;
- complete combinational and disciplined sequential assignments;
- reset values, CDC/RDC, generated clocks, handshakes, backpressure, and recovery;
- arbiters, queues, DMA/memory ordering, partial bursts, address bounds, and ECC/errors;
- synthesizability, black boxes, latches, multiple drivers, X behavior, and parameters;
- third-party IP wrappers/configuration/licenses.

Generated netlists or checked-in Verilog do not replace generator-source review.

## Verification gate

Require an independent oracle and fresh command output. Challenge whether the
reference simply repeats the RTL. Require representative and adversarial scenarios,
random seeds, assertions, X/Z, formal where appropriate, coverage goals, and retained
failure logs/waves.

Numerical accelerator review includes quality/tolerance, overflow, saturation,
rounding, quantization scales, exceptional values, and cross-configuration parity.

Never accept compile-only, one happy-path test, stale output, or weakened expected
values. A red gate blocks PPA and benchmark interpretation.

## PPA and physical-design integrity

Check exact device/PDK, libraries, tools, configuration, clocks/I/O, corners,
utilization, memory inclusion, activity, and power method. Inspect raw timing,
area/resources, power, warnings, black boxes, congestion, and generated artifacts.
Require an incremental ledger for non-SRAM delta area, cells, Fmax, cycles, and
remaining reserve. Challenge dedicated operator blocks when lifetimes permit measured
resource folding, but reject reuse that loses more PPA through mux/control overhead.

For GDS/tapeout claims independently check STA, DRC, LVS, antenna, density, PDN,
IO/package, SRAM/hard macros, and foundry deck provenance. Open-PDK 130nm PPA cannot
be presented as an absolute win over 3–8nm commercial silicon.

## Prototype review

FPGA claims require a real bitstream and named board, tool version, clocks/resources,
host/runtime, on-board correctness, power measurement, and logs. Simulator or
FireSim results must be labeled as such.

Structured N/A must match the delivery level and explain what remains unverified.

## Benchmark fairness

Require identical workload and quality constraints, offload partition, host work,
memory bandwidth/capacity, resource/area budget, clocks, warmup, repetitions,
synchronization, and power method.

Report distributions and separately show:

- kernel cycles/latency, throughput, effective bandwidth, utilization, and energy;
- full-system TTFT, TPOT, tokens/s, watts, joules/token, and quality where available;
- FPGA or same-node PPA;
- commercial market context.

For Gemmini/VTA/NVDLA baselines verify configuration parity and do not compare INT4
candidate arithmetic with unmatched INT8 resources without quality/resource accounting.

## Autonomy and artifact provenance

Require role/DAG/event/checkpoint history, git commits, candidate and rejected attempts,
raw evaluator/PPA logs, hashes, tool identities, and intervention records. The
controller may prepare public inputs and run sealed evaluation, but controller-authored
RTL, repaired outputs, or hidden-oracle feedback invalidate an autonomous-design claim.

## Final decision

Return done only when:

- the declared delivery level is fully supported;
- all preceding stage results are current and hash-bound;
- reproduction works from documented source/tooling;
- claims are bounded to the evidence;
- known limitations and unsupported levels are prominent;
- a maintainer or hardware reviewer can reproduce the result without guessing.

Otherwise return a concrete HOLD/continue reason at the earliest invalid stage.

## Capability-boundary iteration reuse

Reuse Reviewer-certified upstream evidence unless the current delta contradicts
it. Adding support for the next operator is not by itself a product-scope,
architecture, or environment change. Roll back to the earliest genuinely changed
contract and reject ceremonial regeneration of stable upstream packets.

Every reused verification, PPA, or benchmark result must bind the current
`design/RTL_MANIFEST.json` SHA-256. A stale binding is a concrete conflict and
cannot be waived by prose. Non-milestone operator uplifts use the fast
RTL→verification→Sky130-PPA loop; reserve prototype, full benchmark, multi-node
PPA, sign-off, and milestone commits for a complete target hardware workload, a
complete model/system demonstration, or an operator-requested release.
Intermediate operator groups are checkpoints, not release milestones.
