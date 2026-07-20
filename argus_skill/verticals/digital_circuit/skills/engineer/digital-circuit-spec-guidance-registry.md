---
name: Digital Circuit Spec Guidance Registry
description: Enrich visible RTL specifications with compact, reusable hardware invariants before generation without importing benchmark-specific answers.
category: digital-hardware-engineering
version: 1
---

# Digital Circuit Spec Guidance Registry

## Operating method

Apply only detectors supported by the visible prompt and public context. Write
the selected guidance and its evidence into `design/SPEC.md` before RTL edits.
Never name a benchmark task, expected hidden value, or prior solution.

| Visible pattern | Required generic guidance |
| --- | --- |
| Wrapper or modification task | Preserve exact public module, port, parameter names, widths, polarity, and latency. Compile every visible parameter override and instantiate every public wrapper. |
| Counter, timer, divider, pulse | Freeze level-versus-edge control, divider phase, first-event latency, rollover, pause/resume, and one-cycle pulse timing in a cycle table. |
| FSM or protocol controller | Enumerate every state, transition trigger, state-specific output encoding, reset state, timeout, illegal/recovery path, and Moore/Mealy timing. |
| CDC or asynchronous domains | Define source acceptance, synchronization primitive, event/data coherence, backpressure, and the invariant that each accepted source item produces exactly one destination transfer unless the public contract explicitly permits cancellation. |
| Encoder/decoder or line code | Freeze symbol polarity, running state, substitution windows, error timing, relock behavior, and round-trip latency; require a round-trip oracle plus malformed cases. |
| Cryptographic/datapath transform | Freeze endianness, key/data ordering, round/latency protocol, valid/ready timing, reset behavior, and published known-answer vectors. |
| Pure combinational truth table | Prefer deterministic Boolean/K-map or exhaustive construction before LLM search; prove all input combinations when tractable. |

## Contamination boundary

- Missing prompt-referenced public context blocks generation.
- Official failures may diagnose a submitted attempt but must not reconstruct a
  missing public contract.
- Guidance learned from one benchmark phase may be used only after it is
  generalized, reviewed, versioned, and frozen for a later phase.
