---
name: Digital Circuit First-Pass Contract Closure
description: Eliminate avoidable first-attempt RTL failures by freezing exact benchmark interfaces, timing, control semantics, and state/output behavior before generation.
category: digital-hardware-engineering
version: 1
---

# Digital Circuit First-Pass Contract Closure

## Mandatory pre-RTL manifest

Before writing RTL, create `design/BENCHMARK_INTERFACE.json` from public inputs:

- top-level `"status": "ready"` after all public contract fields are resolved;
- exact output file path and top-level module name;
- every port name, direction, width, signedness, and reset value;
- every parameter name, type, default, legal range, and visible override;
- clock/reset names, polarity, async/sync behavior, and release semantics;
- whether each control is level, pulse, edge-toggle, or handshake;
- cycle latency, throughput, valid/ready ordering, and pulse duration;
- FSM state/output encoding table or codec state/running-state invariants.

The RTL file and module declaration must match this manifest byte-for-byte for
identifiers. If public inputs do not determine an item, stop as an incomplete
contract instead of guessing.

## First-pass prevention rules

1. Compile the exact top module named by the manifest.
2. Compile every visible parameter override and wrapper instantiation.
3. Run reset assertion/release and first-event latency smoke tests.
4. Exhaust small FSM/output tables and pure combinational truth tables.
5. For CDC, prove accepted input count equals delivered output count unless
   cancellation is explicitly legal.
6. For encoder/decoder pairs, run legal round-trip, malformed input, polarity,
   running-state, and relock tests.
7. Only after these checks pass may the controller construct an official answer
   or invoke a scorer.

Before handoff, write `evidence/preflight.json` with `"status": "pass"`, the
exact top module, RTL source paths, compiler command/return code, and output
schema mapping. Any unresolved issue keeps status `"blocked"`.

Do not add compatibility aliases merely to guess hidden interfaces. Exact public
contract fidelity is preferable; missing public context is a packaging defect.
