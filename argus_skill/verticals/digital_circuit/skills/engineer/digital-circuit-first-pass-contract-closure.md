---
name: "Digital Circuit First-Pass Contract Closure"
description: "Eliminate avoidable first-attempt RTL failures by freezing exact benchmark interfaces, timing, control semantics, and state/output behavior before generation."
---

# Digital Circuit First-Pass Contract Closure

## Mandatory pre-RTL manifest

Before writing RTL, create `design/BENCHMARK_INTERFACE.json` from public inputs:

- `"schema_version": 2` so the strict typed manifest contract is explicit;
- top-level `"status": "ready"` after all public contract fields are resolved;
- exact output file path and top-level module name;
- every port name, direction, width, signedness, and reset value;
- every parameter name, type, default, legal range, and visible override;
- clock/reset names, polarity, async/sync behavior, and release semantics;
- whether each control is level, pulse, edge-toggle, or handshake;
- cycle latency, throughput, valid/ready ordering, and pulse duration;
- FSM state/output encoding table or codec state/running-state invariants.
- `"ambiguities"` as an explicit list (empty only when the public contract is
  unambiguous), plus `"interface_change": {"requested": false}` unless the
  public prompt explicitly requests an interface bug fix. A requested change
  also records the public request text or locator in `"public_request"`.

The RTL file and module declaration must match this manifest byte-for-byte for
identifiers. Preserve the prompt's module and port contract even when another
interface seems more conventional; never silently “correct” it. If public inputs
do not determine an item, record the ambiguity and stop as an incomplete contract
instead of guessing.

## First-pass prevention rules

1. Compile the exact top module named by the manifest.
2. Compile every visible parameter override and wrapper instantiation.
3. Check every exact width and signed/unsigned conversion, including intermediate
   expression sizing, truncation, extension, comparison, and literal sizing.
4. Distinguish combinational current-input behavior from clocked prior-state
   behavior. Run reset assertion/release, reset polarity/synchronicity, first-event,
   and every documented latency smoke test.
5. Treat uninitialized state as uncertain unless the public contract defines
   initialization; use X-aware assertions or multiple legal initial states.
6. Exhaust small FSM/output tables and pure combinational truth tables. Otherwise
   use public-contract metamorphic checks such as idempotence, round-trip,
   conservation, ordering, or equivalent transformations.
7. For CDC, prove accepted input count equals delivered output count unless
   cancellation is explicitly legal.
8. For encoder/decoder pairs, run legal round-trip, malformed input, polarity,
   running-state, and relock tests.
9. Only after these checks pass may the controller construct an official answer
   or invoke a scorer.

Before handoff, write `evidence/preflight.json` with `"status": "pass"`, the
exact top module, RTL source paths, compiler command/return code, and output
schema mapping. Any unresolved issue keeps status `"blocked"`.

Do not add compatibility aliases merely to guess hidden interfaces. Exact public
contract fidelity is preferable; missing public context is a packaging defect.
