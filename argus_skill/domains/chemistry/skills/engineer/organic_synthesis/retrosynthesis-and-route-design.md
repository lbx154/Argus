---
name: "Organic Retrosynthesis and Route Design"
description: "Design small-molecule organic synthesis routes using target identity, disconnections, precedent, selectivity, supply, safety, and evidence; excludes enzyme pathways, MOF assembly, and generic prediction."
---

## When to use

Use for retrosynthetic analysis, route ideation, route rescue, make-versus-buy
decisions, analogue synthesis planning, or comparison of literature and
machine-generated routes.

## Do not use when

Do not use as proof that a target can be synthesized. Do not use for metabolic
pathway engineering, polymer processing, framework topology design, or
biochemical assay planning. A route-search score is not experimental evidence.

## Scientific question

Define target structure and acceptable form, scale, purity, stereochemical
requirements, available starting materials, prohibited chemistry, equipment,
time/cost constraints, and whether the output is ideation, precedent-backed
planning, or an execution-ready proposal requiring human authorization.

## Required inputs

Target identity; known analogues and prior routes; inventory and sourcing rules;
allowed reaction classes; scale and facility constraints; hazard exclusions;
required evidence bar; and route comparison objectives.

## Decision procedure

1. Resolve target stereochemistry, protonation, salt/solvate, and protecting
   group constraints.
2. Search for exact target, close analogues, and transformation precedents.
3. Identify strategic bonds and functional-group relationships.
4. Generate multiple disconnection families before optimizing one route.
5. For each step, propose substrates, transformation class, selectivity risks,
   protecting-group implications, and precedent.
6. Check starting-material availability and whether claimed purchasability is
   current and jurisdiction-appropriate.
7. Evaluate convergence, longest linear sequence, redox/protecting-group burden,
   isolation difficulty, hazardous operations, robustness, and scale transfer.
8. Rank routes under the stated objectives and retain rejected alternatives.

## Tool-selection ladder

Use literature and reaction databases for precedent; deterministic structure
tools for identity and substructure checks; an authorized route-search engine
for candidate generation; forward prediction only as a model-based check; and
expert mechanistic reasoning for interpretation. Verify tool versions, training
domain, and license before relying on rankings.

## Minimum capability probe

Confirm target parsing, stereochemistry retention, building-block policy, and
one known transformation. Verify that a search tool can return source-linked
steps and that inaccessible proprietary corpora are not implied.

## Evidence to retain

Keep route structures, atom mappings when used, source-linked precedents,
conditions and scope limitations, model/tool outputs, alternative routes,
rejection reasons, and unresolved hazards.

## Validation gates

- Every proposed step has either applicable precedent or an explicit speculative
  label with a validation experiment.
- Check chemo-, regio-, stereo-, and functional-group compatibility across the
  full sequence, not step by step in isolation.
- Do not present database reaction frequency or model confidence as yield.
- Do not hide unavailable reagents, unstable intermediates, impossible
  selectivity, or unsupported protecting-group assumptions.
- Route comparison uses the same target form, scale, and objectives.

## Safety and authorization

Planning does not authorize procurement or execution. Flag energetic,
pyrophoric, toxic, pressurized, cryogenic, gas-generating, controlled, and
scale-sensitive operations for facility review.

## Output contract

Provide target identity, route options, stepwise transformations, precedent and
scope, selectivity/mechanism risks, sourcing assumptions, safety flags,
comparison criteria, confidence by step, and experiments needed to de-risk the
preferred route. Label the result `conceptual`, `precedent-backed`, or
`execution-candidate`; never `validated` without physical evidence.

## Stop, block, or replan conditions

Replan when no route meets hard facility or safety constraints, the key step
lacks applicable precedent and a feasible validation path, or target identity
and stereochemistry are unresolved.

## Official references

- Open Reaction Database: https://open-reaction-database.org/
- AiZynthFinder documentation: https://molecularai.github.io/aizynthfinder/
- ACS chemical safety: https://www.acs.org/chemical-safety.html
