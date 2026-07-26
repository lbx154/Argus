---
name: Chemistry Playground Bounded Hypothesis Probe
description: Run an explicitly requested Chem Playground candidate as a bounded, computation-first hypothesis probe with QUESTION/RESULT artifacts, evidence labels, negative-result retention, and mandatory Reviewer gate.
category: chemistry-playground
version: 1
protected: true
---

## When to use

Use only when the user or Planner explicitly requests a **Chem Playground**
candidate for a risky, speculative, or unconventional chemical hypothesis and a
bounded literature/computational probe can change whether the idea deserves
formal research attention.

## Do not use when

Do not use for routine Chemistry research, ordinary literature review, standard
analysis, production decisions, formal Research-stage evidence, or any task that
would directly execute a physical experiment, control an instrument, or bypass
authorization. Do not create a Playground merely because a task is uncertain.

## Scientific question

State one falsifiable hypothesis, its decision-relevant observable, explicit
assumptions, credible competing explanations, and what result would weaken or
falsify it. Preserve bold speculation as a candidate, never as established fact.

## Required inputs

- an explicit Playground request and a unique lowercase hyphenated `idea-id`;
- the original scientific question and hypothesis;
- evidence and capability inventory;
- a bounded compute, wall-time, query, and iteration budget;
- non-goals, claim ceiling, safety boundary, and stopping conditions.

## File protocol

Use exactly:

```text
research/chem_playground/<idea-id>/
├── QUESTION.md
├── work/
│   ├── scripts/
│   └── notebooks/
├── evidence/
│   ├── inputs/
│   └── outputs/
└── RESULT.md
```

Create a new candidate with:

```text
python -m argus_skill.domains.chemistry.playground init \
  --project-root . --idea-id <idea-id> \
  --question "<question>" --hypothesis "<hypothesis>"
```

Never overwrite or reuse an existing candidate directory. Work only inside that
candidate except for reading project-native inputs. Keep imported originals in
`evidence/inputs/`, generated primary outputs in `evidence/outputs/`, and
reproducible code in `work/`.

## Decision procedure

1. Complete `QUESTION.md` before claim-critical probing.
2. Search and retain only decision-relevant grounding; record conflicts and
   source quality.
3. Choose the cheapest discriminating deterministic or computational probe.
4. Run a representative capability probe before consuming the stated budget.
5. Record full settings, versions, primary outputs, warnings, convergence,
   sensitivity, failures, and negative results.
6. Update `RESULT.md` and advance only through legal protocol states:
   `speculative`, `literature_grounded`, `computationally_probed`,
   `reviewer_candidate`.
7. Before handoff, keep `reviewer: pending` and
   `reviewer_recommendation: pending`, then run:

```text
python -m argus_skill.domains.chemistry.playground validate \
  --project-root . --idea-id <idea-id>
```

8. Request independent review. Do not self-assign `promoted`, `retained`,
   `falsified`, or `blocked`.

## Evidence to retain

Use the RESULT evidence ledger form:

```text
- [retrieved|curated|predicted|computed|simulated|measured|inferred|negative|failed] path-or-URL - claim supported
```

Preserve raw inputs, outputs, code, environment/configuration, assumptions,
controls, alternative explanations, sensitivity, uncertainty, dead ends, and
budget use. Imported measured evidence remains measured; Playground computation
must never be relabeled as measurement.

Store `retrieved`, `curated`, and imported `measured` files below
`evidence/inputs/`. Store `predicted`, `computed`, `simulated`, `inferred`,
`negative`, and `failed` artifacts below `evidence/outputs/`. References use:

```text
- [reference] relative/path-or-HTTP(S)-or-DOI - purpose
```

## Validation gates

- `idea-id`, required headings, paths, references, and state history pass the
  deterministic validator.
- Every local evidence reference exists inside the candidate directory.
- Literature grounding traces claims to inspectable sources.
- Computational states retain predicted/computed/simulated primary output.
- The claim does not exceed the evidence class or applicability domain.
- `CHECKPOINT.md` names QUESTION, RESULT, and decisive evidence paths.
- Independent Reviewer edits the final status and reruns the validator.

## Common failure modes

Do not hide failed tools, cherry-pick only supportive probes, turn a notebook
display into primary evidence, infer mechanism from one compatible result, spend
beyond the declared budget, or silently move a candidate into the formal
Research pipeline.

## Uncertainty and applicability domain

Report parameter sensitivity, model chemistry, finite-size/time and sampling
limits, source conflicts, untested alternatives, and conditions under which the
probe ceases to discriminate the hypothesis.

## Safety and authorization

The Playground authorizes no synthesis, assay, cell cycling, instrument action,
robotics, procurement, or scale-up. Physical evidence may be read if already
authorized and identified, but no file or Reviewer verdict grants physical
authority.

## Output contract

Deliver a valid candidate directory with a complete QUESTION, reproducible
bounded work, primary evidence, RESULT, legal status history, retained failures,
and an independent Reviewer decision. A negative or falsifying result is a valid
bounded outcome.

## Stop, block, or replan conditions

Stop at budget exhaustion or decisive falsification. Block on unsafe/unauthorized
physical work, missing claim-critical inputs, unavailable licensed capability,
unresolvable identity, or non-inspectable evidence. Replan when the probe cannot
discriminate alternatives or the requested claim belongs in ordinary Research
rather than the Playground.

## Official references

- FAIR Principles: https://www.go-fair.org/fair-principles/
- NIST Research Data Framework: https://www.nist.gov/programs-projects/research-data-framework-rdaf
- Crossref DOI metadata: https://www.crossref.org/documentation/
