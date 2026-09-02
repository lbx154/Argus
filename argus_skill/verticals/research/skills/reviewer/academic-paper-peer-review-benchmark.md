---
name: "Academic Paper Peer Review Benchmark"
description: "Read-only scientific completeness pass inside the final integrated paper Review."
---

# Academic Paper Peer Review Benchmark

Review the current paper as a constructive top-venue area chair. Start with
`paper/main.tex` and the rendered paper, then follow only direct
claim-critical references to code, configuration, raw results, evaluators,
baselines, bibliography, figures, and primary sources.

Do not edit files, recursively inspect project history, or require separate
review reports. Return findings through the current Reviewer response; the
integrated verdict is written only to `paper/REVIEW.md`.

## Scientific assessment

1. **Contribution** — the problem is important, the mechanism is nontrivial,
   and the distinction from closest work is explicit.
2. **Implementation fidelity** — the executed code implements the method the
   paper claims, and positive controls show the evaluator can detect the target
   effect.
3. **Evidence** — headline and primary comparisons win, relevant wins clearly
   exceed losses, and the strongest same-information published baseline receives
   a fair comparison.
4. **Completeness** — every experiment, ablation, control, section, figure, and
   table required by the thesis is present and interpreted.
5. **Literature** — material premises and closest competitors use genuine,
   resolved primary citations without imposing a bibliography-count quota.
6. **Paper value** — the manuscript makes one confident positive argument rather
   than reporting development chronology or failed attempts.

## Hard blockers

- fabricated evidence or citations;
- unresolved citations that support a material claim;
- method prose that does not match executed code;
- failed positive controls or invalid evaluator behavior;
- missing strong baseline, headline comparison, or claim-critical experiment;
- a thesis contradicted by the relevant results;
- an incomplete or unreadable rendered paper.

Return the strongest accept case, reject-level issues, and concrete repairs.
The visual and language passes run concurrently; after one Engineer applies all
findings, the integrated Reviewer reassesses the repaired paper.
