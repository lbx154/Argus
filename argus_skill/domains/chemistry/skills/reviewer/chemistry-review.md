---
name: Chemistry Research Review
description: Add chemistry-specific independent review criteria to research missions covering chemical fidelity, evidence, controls, uncertainty, reproducibility, and claim boundaries.
category: chemistry-review
version: 1
---

Review the chemistry, not the paperwork. Missing manifests, ledgers, audit packets,
or prescribed filenames are not defects by themselves, and their presence is not
evidence that the result is correct.

Read the original question, actual inputs, source data, code, primary tool or
instrument outputs, and the claimed result. Check molecular, reaction, reagent,
or sample identity and the observable. Where relevant, inspect canonicalization,
structures and stereochemistry, protonation and tautomer state, charge and spin,
units, assay or reaction conditions, approximations, method and basis choices,
calibration, convergence, controls, repeats, and uncertainty.

Distinguish retrieval from prediction, surrogate-oracle output from a fresh
calculation, retrospective data from a prospective experiment, and simulation
from physical measurement. A route planner does not prove synthetic feasibility;
a database record does not prove a new observation; a clean process exit does not
prove that scientific settings were valid.

For optimization and discovery, compare against the strongest appropriate
baseline under the same budget. Check whether hidden answers, public benchmark
labels, test structures, or future data leaked into proposal decisions. Review
the full trajectory, including failed and negative observations, rather than only
the best endpoint.

Audit the claimed agent involvement. A policy designed once by an agent and then
executed as fixed code measures that frozen policy, not online agent decisions.
Require the result to identify whether control was online, periodically revised,
or frozen before outcomes; do not let the label "Argus-guided" blur that boundary.
When the operator asked to evaluate online Argus control, reject a frozen policy
as the wrong experiment even if its implementation and statistics are correct.

Audit the evaluator threat model separately from its API shape. A same-user
subprocess, file mode, import boundary, or unchanged hash can show cooperative
protocol compliance and provenance, but does not adversarially seal answers from
an agent that can read or edit the same workspace. Certify a sealed or anti-cheat
claim only when external or OS-enforced access controls actually prevent that
path; otherwise require narrower wording.

Physical execution must be traceable to an externally authorized capability and
facility or instrument interlocks. Do not infer safety from fluent prose, and do
not certify a physical result that was only planned or simulated.

Return `done` only when the evidence supports the requested bar. State plainly
what was observed, computed, predicted, reproduced, improved, falsified, or left
unresolved. A bounded negative result can complete that experiment, but honesty
alone cannot complete the research objective. If it lacks standalone decision
value, return `replan_requested`; use `done` only for a valuable supported thesis. Check
primary sources when novelty is claimed or required; otherwise leave novelty
unknown rather than demanding another process artifact.
