---
name: "Hypothesis-Implementation Contract"
description: "Bind a selected research idea to the exact code path that will test it before claim-bearing execution."
---

# Hypothesis-Implementation Contract

Use this after one idea is selected and before writing or changing experimental
code. Its purpose is to prevent an attractive hypothesis from silently becoming
an easier implementation.

Write one canonical artifact:
`research/HYPOTHESIS_IMPLEMENTATION_CONTRACT.md`.

## Freeze the scientific object

Copy the selected thesis and binding prediction without strengthening or
simplifying them. State:

- the causal or formal objects and assumptions;
- the intervention, treatment, decision, or transformation;
- the estimand or proof obligation;
- the expected direction or falsifier;
- the strongest alternative explanation;
- information available to the method at decision time;
- invariants that the method must preserve.

Do not run an experiment while producing this contract.

## Map it to implementation

For every load-bearing hypothesis element, record:

| Hypothesis element | Scientific meaning | Planned code path | Config/data field | Observable output | Baseline/control | Required invariant |
|---|---|---|---|---|---|---|

Use concrete entry points, functions, branches, tensors/records, configuration
keys, and output columns. Map formulas symbol by symbol, including operands,
signs, masks, reductions, gradient boundaries, timing, and scope. A function or
variable with a plausible name is not a mapping.

The contract must also identify:

- the command that will execute the method;
- where the candidate and each baseline diverge;
- how the selected intervention is applied online rather than reconstructed
  after outcomes are known;
- how train, development, and held-out data remain separated;
- which output proves the claimed branch actually ran;
- what implementation result would mean `NOT_IMPLEMENTED` rather than a
  negative scientific finding.

## Implement, then review before execution

After the contract is frozen, implement the method. A fresh Reviewer compares
the selected idea, this contract, and the actual entry-point call chain and
records one verdict in the same artifact:

- `ALIGNED`: the implemented path computes the selected mechanism with matching
  operands, scope, timing, baselines, and invariants.
- `MISMATCH`: code executes, but it tests a different mechanism or comparison.
- `NOT_IMPLEMENTED`: the selected mechanism is absent or unreachable from the
  planned entry point.

`MISMATCH` and `NOT_IMPLEMENTED` have two honest exits: change the code to
implement the selected idea, or revise the idea and experiment plan before
result-producing execution. Never reinterpret later outcomes to make the
hypothesis fit the code.

Unit and smoke checks may verify imports, shapes, branches, and evaluator
plumbing after implementation. They do not test or rank the scientific idea.
The first scientific comparison belongs to the planned claim-relevant
experiment.

## Preserve changes prospectively

If implementation constraints require a scientific change, update the contract
and plan before inspecting affected results, and retain the prior wording in a
short change-history section. Routine refactors that preserve the mapped
computation need no new contract.

After results exist, the separate `Claim-to-Code Trace` follows the actual
producing command and runtime path. This pre-execution contract prevents drift;
the post-execution trace verifies what really ran.
