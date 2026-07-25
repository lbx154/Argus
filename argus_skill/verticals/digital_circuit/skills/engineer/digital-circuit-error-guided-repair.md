---
name: Digital Circuit Error-Guided Repair
description: Classify RTL failures, choose the smallest evidence-supported repair strategy, and preserve cumulative correctness across a fixed iteration budget.
category: digital-hardware-engineering
version: 1
---

# Digital Circuit Error-Guided Repair

## Failure taxonomy

Classify the first decisive failure before editing:

1. `compile/elaboration`: syntax, unsupported construct, missing module, parameter override.
2. `interface-contract`: port/parameter name, width, signedness, polarity, hierarchy.
3. `reset-clock`: reset type/polarity/release, divider phase, edge sampling.
4. `temporal-protocol`: latency, valid/ready, stall, pulse duration, ordering.
5. `state-output`: FSM transition or state-specific output encoding.
6. `cdc-transfer`: lost, duplicated, incoherent, or metastability-unsafe transfer.
7. `datapath-arithmetic`: value, overflow, truncation, endianness, algorithm.
8. `benchmark-packaging`: required public context or tool input is missing.
9. `evaluator-infrastructure`: the official harness did not execute, returned no
   run, or failed outside RTL compilation/simulation. This is not an RTL verdict.

## Repair loop

1. Preserve the immutable attempt and sealed failure log.
2. State one root-cause hypothesis and the evidence that distinguishes it.
3. Make one narrow RTL change; never change visible inputs, reference, or scorer.
4. Add a task-local regression that fails before and passes after the change.
5. Rerun compile plus the smallest functional gate, then independent Reviewer.
6. Append the result to the attempt ledger and update cumulative correctness for
   every allowed iteration.
7. Record only the categorical official signature status (`changed`, `unchanged`,
   `unavailable`, or `no_execution`), never mismatch vectors or hidden behavior.
8. When the signature is unchanged, write a changed public-only hypothesis JSON
   and a changed task-local regression/metamorphic test. Both must identify
   `provenance_scope=public_only`, `changed_from_prior=true`, and the current
   `generation` / `iteration` / `repair_mission_id`; wording-only RTL regeneration
   cannot satisfy the repair gate.
9. Stop on success or the predeclared cap. Repeated identical failures require a
   different hypothesis, not another wording-only regeneration.

Do not treat tool/backend refusal, missing public context, or stale packaging as
an RTL failure. Surface those environmental defects separately, and do not infer
correctness or incorrectness when the evaluator never executed.
