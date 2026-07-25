---
name: Chip Design Environment-First Engineering
description: Design and optimize a digital chip or accelerator through workload definition, architecture, RTL, verification, PPA, prototyping, benchmark comparison, and sign-off while proving the exact EDA/PDK/IP environment and preserving auditable evidence.
category: chip-design
priority: high
version: 1
created_at: 2026-07-20T00:00:00+00:00
---

# Chip Design Environment-First Engineering

## Core principle

A chip-design result is only as strong as its declared delivery level and raw tool
evidence. Synthesizable RTL is not an FPGA prototype; FPGA timing is not ASIC PPA;
open-PDK GDS is not fabricated silicon; and vendor marketing TOPS is not a fair
workload comparison.

Treat the environment, reusable IP, compiler/runtime, PDK, constraints, memory
model, verification oracle, and benchmark protocol as part of the design.

## Required workflow

### 1. Freeze the product and workload

Before architecture or RTL:

- read repository instructions and existing hardware/software;
- write `design/CHIP_SCOPE.json`, `design/WORKLOAD.md`, and `design/SPEC.md`;
- choose `delivery_level`: `rtl_ip`, `fpga`, `gds`, `pre_tapeout`, or `tapeout`;
- pin model/operators, tensor shapes, quantization, quality floor, host work,
  external-memory assumptions, clocks, interfaces, numerical formats, and
  acceptance metrics;
- name open-hardware baselines and commercial market references separately;
- write non-goals so a proof-of-concept cannot silently become a production claim.

For edge LLM inference, explicitly separate prefill and batch-1 decode. Decode
is usually memory-bandwidth-bound: quantify weight and KV bytes per token before
choosing a systolic array merely because it advertises high TOPS.

### 2. Model the architecture before RTL

Write:

- `design/ARCHITECTURE.md`;
- `design/MEMORY_MODEL.json`;
- `design/BASELINE_PLAN.md`.

Quantify:

- operations and bytes per representative workload;
- arithmetic intensity and roofline bound;
- array/lane utilization, pipeline occupancy, and Amdahl leverage;
- SRAM capacities/banks/ports, DMA widths/bursts, double buffering, and external
  bandwidth;
- host/accelerator partition and compiler/runtime commands;
- latency, throughput, backpressure, reset, errors, CDC, and completion;
- accumulation precision, scaling, saturation, overflow, and quality impact.

Before adding dedicated hardware, write a lifetime/resource-sharing table. Evaluate
whether mutually exclusive operators can share or fold MAC lanes, accumulators,
requantization, vector/SFU, divider, round/saturate, DMA, buffers, and control.
Measure the mux/control and cycle cost: reuse is a PPA hypothesis, not an automatic win.
Keep an append-only frontier ledger with non-SRAM delta area/cells, Fmax, cycles,
remaining area reserve, and the exact RTL/constraint hashes. Stop repeated local tweaks
when gains saturate and escalate to structural folding or an honest Pareto/no-go result.

Compare a proposed edge-LLM accelerator against at least one reusable open design
such as Gemmini or VTA under matched resources. Keep Jetson/Hailo/Apple/Qualcomm
as system references unless the same workload is physically measured.

### 3. Audit tools, PDKs, and IP

Query the curated registry:

```bash
"${ARGUS_SKILL_PYTHON:-python}" -m \
  argus_skill.verticals.chip_design.environment_audit catalog \
  --list-categories
"${ARGUS_SKILL_PYTHON:-python}" -m \
  argus_skill.verticals.chip_design.environment_audit catalog \
  --category accelerator_ip
"${ARGUS_SKILL_PYTHON:-python}" -m \
  argus_skill.verticals.chip_design.environment_audit catalog \
  --category physical_design
```

Write `research/TOOLCHAIN_CANDIDATES.md`, `research/IP_REUSE_PLAN.md`, and
`design/TARGET.json`. Record tool/IP licenses and pinned revisions.

Collect from the exact runtime:

```bash
"${ARGUS_SKILL_PYTHON:-python}" -m \
  argus_skill.verticals.chip_design.environment_audit collect \
  --project-root . --target-python .venv/bin/python
"${ARGUS_SKILL_PYTHON:-python}" -m \
  argus_skill.verticals.chip_design.environment_audit check \
  --project-root .
```

A red capability blocks that stage. Prefer project containers/Nix/CI and isolated
toolchains over mutating shared installations.

### 4. Build traceable RTL

Maintain `design/RTL_MANIFEST.json` with:

- source and generated files;
- top modules, parameters, interfaces, and clock domains;
- third-party/generator provenance and licenses;
- exact regeneration commands.

Implement one architecture increment at a time. Use explicit widths and signedness,
complete combinational assignments, disciplined sequential logic, bounded indices,
safe CDC/reset, and synthesizable constructs. Generated RTL is an artifact, not the
authoritative source, unless the contract explicitly says otherwise.

### 5. Verify independently

Write `verification/PLAN.md` before implementation closes. Use an independent
Python/C++/SystemC model or formal properties rather than copying the RTL algorithm.

Cover:

- unit and integrated datapaths/controllers;
- reset entry/exit and all clocks;
- protocol stalls, backpressure, errors, and recovery;
- minimum/maximum/irregular shapes and parameters;
- numerical tolerances, overflow, saturation, and quantization quality;
- randomized seeds, assertions, X/Z, CDC, safety/liveness, and coverage;
- memory ordering, DMA boundaries, partial bursts, and unaligned cases.

Write `verification/RESULTS.json` with successful command exits and raw artifacts.
Never weaken the oracle after a failure.

### 6. Produce fair PPA

Write `ppa/PROTOCOL.md` before synthesis. Pin:

- FPGA/PDK/library and tool revisions;
- configuration and memory treatment;
- clocks, I/O, corners, utilization, activity, and power method;
- baseline configuration.

Write `ppa/RESULTS.json` with timing/Fmax, area/resources, memory, power or an
explicit power limitation, warnings/waivers, and raw reports. For GDS/tapeout,
include placement/routing, congestion, extraction, STA, power, signal integrity,
IR-drop/EM, DRC, LVS, antenna, density, and hashes. A `pre_tapeout` result must also
include executable lint, CDC/RDC, formal, equivalence, DFT/scan/ATPG, synthesis,
floorplan/PDN/place/CTS/route, extraction, and physical-verification evidence. It is
still not a foundry submission or fabricated device.

Do not compare different nodes as direct area/frequency wins. Report normalized
context separately.

### 7. Prototype at the declared level

Write `prototype/RESULTS.json`.

FPGA evidence includes bitstream, board/tool identity, clocks/resources, host/runtime,
on-board correctness, power, and raw logs. GDS evidence includes layout and sign-off
artifacts. Structured `not_applicable` is valid only when the frozen delivery level
does not require that prototype.

### 8. Benchmark the real workload

Write `benchmark/PROTOCOL.md` before timing. Match candidate and baselines on:

- model, shapes, quantization, quality/perplexity floor;
- host preprocessing/postprocessing and offload boundary;
- memory bandwidth/capacity and resource/area budget;
- warmup, repetitions, synchronization, clocks, and power method.

Report kernel and system metrics separately. For an edge LLM accelerator include
GEMV latency/bandwidth/utilization and, when end-to-end is available, TTFT, TPOT,
tokens/s, watts, and joules/token. Preserve distributions and regressions.

### 9. Sign off without overstating

Write `signoff/ARTIFACT_MANIFEST.json`, `signoff/SIGNOFF.json`, and `RESULTS.md`.
Hash-bind source and decisive artifacts, record reproduction commands, licenses,
known limitations, failed attempts, Argus role trajectories, and operator
interventions. State exactly which delivery level is certified.

## Iteration discipline

For architecture/PPA optimization:

1. measure a bottleneck and expected whole-system leverage;
2. preregister one mechanism;
3. preserve the candidate diff/config;
4. run the cheapest decisive verification first;
5. run full verification before PPA/benchmark interpretation;
6. compare under matched constraints;
7. record a two-axis outcome: execution/failure status versus idea status;
8. retain or reject through independent review.

Do not spend later attempts on unchanged reruns, cosmetic RTL rewrites, or tool
knob sweeps without a physical hypothesis.

## Reuse across capability-boundary iterations

After a reviewed checkpoint, preserve certified upstream evidence and reopen only
the earliest stage whose contract changed. Moving the first unsupported operator
while workload, delivery level, numerical behavior, interfaces, budgets,
architecture, target, and toolchain stay fixed reopens `rtl`; it does not reopen
definition, architecture, or environment.

Use the fast capability loop for non-milestone operator uplifts:
RTL→verification→fresh Sky130 PPA. Run prototype, full benchmark, multi-node PPA,
sign-off, and a local milestone commit only for a complete target hardware
workload, a complete model/system demonstration, or an operator-requested
release. Intermediate operator groups are checkpoints, not release milestones.
Reference
prior certified artifacts by hash instead of rewriting them, and never reuse a
verification/PPA/benchmark result whose recorded RTL-manifest hash is stale.
