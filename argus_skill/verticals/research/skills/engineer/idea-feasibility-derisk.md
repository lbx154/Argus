---
name: "Selected-Idea Implementation Readiness"
description: "After idea selection and implementation review, check only concrete wiring or external availability without using a miniature experiment to judge the scientific idea."
---

# Selected-Idea Implementation Readiness

## Purpose

Never use this skill during idea generation, route review, or selection. After
one idea is selected, its hypothesis-to-implementation contract is frozen, and
the implementation has received independent alignment review, use this skill
only for a concrete wiring, data-shape, evaluator-availability, or resource
readiness question.

Do not run a tiny scientific comparison or premise experiment. Readiness checks
cannot promote, reject, replace, or downgrade an idea and cannot appear as paper
evidence. They do not replace the selection requirement for a nontrivial
technical core, verified originality, claim-relevant formal or causal grounding,
and field-level consequence. This preserves the project's formal/causal grounding
standard without turning readiness into another selection pass.

## How to work

1. Read the selected idea, experiment plan, and
   `research/HYPOTHESIS_IMPLEMENTATION_CONTRACT.md`. Identify one operational
   readiness question whose answer cannot change scientific selection.
2. Use a unit, import, schema, shape, compiler, evaluator, or resource check.
   Do not compare candidate and baseline quality, estimate an effect, or test the
   binding scientific premise.
3. Record the setup before running: model/system identity, data slice, comparator
   or control, metric/observation, and the limitations of the probe.
4. Before any paid or model-backed call, inspect the prediction boundary:
   - candidate and baseline code may receive only information available at their
     claimed decision time, never gold labels, expected outcomes, scorer verdicts,
     or fields derived from them;
   - remove or permute hidden labels and confirm candidate predictions do not
     change;
   - execute baselines with the same information and intervention timing. A
     historical trace that already executed an action or a post-hoc verifier is a
     diagnostic, not an online prevention baseline.
5. Run the readiness check or record the concrete blocker. Preserve the command
   and output under the existing experiment log convention.
6. Write a short factual note in `research/RESEARCH_BRIEF.md` or the existing
   experiment log:
   - what was observed;
   - what remains uncertain;
   - plausible explanations, including implementation weakness;
   - paths to the raw material.

Do not emit a scientific PASS/FAIL, force a pivot, or schedule another idea.
A wiring check is not evidence for or against the thesis.

## Integrity

Never type expected numbers as results, hide failures, or relabel readiness
output as public evidence. Do not read gold labels or scorer-derived fields.

## Handoff

Report the observation and its limits in ordinary prose, with paths to the raw
run. Avoid verdict packets and workflow language.
