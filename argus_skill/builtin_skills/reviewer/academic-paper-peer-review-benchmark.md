---
name: Academic Paper Peer Review Benchmark
description: Simulate a strict, venue-aware reviewer for a nearly complete AI research paper, judging contribution value, evidence, reproducibility, writing, format, and readiness without requiring a positive result or a fixed benchmark scale.
category: paper-review
version: 2
created_at: 2026-07-17T00:00:00+00:00
---

# Academic Paper Peer Review Benchmark

## Purpose

Review a nearly complete paper against its selected venue and actual
contribution shape. Use `research/VENUE_PROFILE.json`,
`research/VENUE_SELECTION.md`, and the official author kit rather than assuming
EMNLP or AAAI.

A method improvement, system, theorem, diagnostic, characterization,
interpretability result, benchmark/data contribution, clean null, negative
result, and boundary finding are all legitimate when they have research value
and appropriate evidence.

## Reviewer stance

- Be skeptical about unsupported claims, not biased toward positive results.
- Do not reward a PDF merely for existing.
- Do not automatically reject diagnostic, null, negative, single-benchmark, or
  focused mechanism studies. Judge whether the evidence is sufficient for the
  exact scope of the contribution.
- Broader claims require broader validation; narrow claims may be supported by a
  focused public benchmark plus decisive controls.
- Every weakness must identify a concrete repair or a justified scope boundary.

## Required inputs

- selected venue profile and official template source;
- manuscript source/PDF/log;
- canonical literature grounding;
- public benchmark/data provenance;
- raw experiment or proof artifacts;
- claims-evidence mapping and generated tables/figures;
- format, academic-language, infrastructure, and layout reviews;
- submission assurance.

## Eight review dimensions

Score each dimension 1–5.

1. **Contribution and research value**
   - Is the question important for the selected venue and subfield?
   - Does the result add a method, mechanism, theory, reliable diagnosis,
     evaluation capability, data resource, negative finding, or useful boundary?
   - Is the contribution distinguished from the closest prior work?

2. **Claim-evidence alignment**
   - Does every headline, numerical, comparative, causal, or generalization claim
     map to authentic evidence?
   - Are nulls, losses, contradictions, and limitations preserved?
   - Is claim scope no broader than the evidence?

3. **Experiment or proof integrity**
   - For empirical claims, is at least one appropriate public benchmark,
     dataset, task suite, challenge, or official evaluation release actually
     executed?
   - Are synthetic/generated diagnostics clearly supplementary?
   - Are the strongest relevant comparisons fair?
   - Is evidence breadth, task count, model count, seed count, or proof coverage
     justified by the claim rather than a universal quota?
   - Are uncertainty and repeatability handled appropriately?

4. **Literature and novelty**
   - Are material premises, nearest competitors, foundations, contradictions,
     and the frontier grounded in primary sources?
   - Does the bibliography contain at least 35 verified BibTeX entries and at
     least 30 unique cited keys, with claim-complete coverage rather than padding?
   - Does the paper explain why the result matters?

5. **Reproducibility**
   - Can an outside researcher identify public data, method/configuration,
     evaluator, controls, uncertainty method, and relevant compute?
   - Are generated artifacts fresh and traceable to canonical sources?
   - Are private paths, secrets, and authoring infrastructure excluded while
     legitimate scientific environment details remain?

6. **Writing and structure**
   - Does the abstract state problem, gap, contribution shape, evidence, and
     implication honestly?
   - Does the paper have one coherent thesis?
   - Are negative or diagnostic results framed as findings rather than apologies?

7. **Venue format and visual evidence**
   - Does the paper follow the selected venue's current official template,
     anonymity policy, page/word limits, bibliography rules, and required
     sections?
   - Are references, tables, captions, and floats readable and internally valid?
   - Data/metric/result plots may be generated from scripts.
   - Every non-data paper-facing figure must use actual image-2/codex-image2
     raster output in `paper/main.tex`. The prompt must come from
     `argus-image2-paper-prompt-v1` and
     `paper-framework-figure-studio-pro-v3.1.4a`, with 6–20 reviewed Figma-style
     layout variants.
   - Hard reject self-drawn non-data substitutes from matplotlib,
     FancyBboxPatch, TikZ node graphs, SVG/PIL/HTML canvas, screenshots,
     Inkscape/manual vectors, or cleaned PDFs.

8. **Strongest reviewer objection**
   - State the strongest short reason to reject.
   - Decide whether it requires new evidence, a source repair, a scope change, or
     only writing/format work.

## Recommendation mapping

Compute the mean dimension score and assign Overall 1–10.

- **8–10**: strong/award-quality candidate.
- **6–7**: accept-quality for the selected venue; evidence supports the scoped
  contribution and no hard blocker remains.
- **5**: borderline; one material objection remains.
- **3–4**: reject; major evidence, novelty, integrity, or presentation gap.
- **1–2**: strong reject; fabricated evidence, fatal flaw, or no research value.

`Decision: Accept` requires Overall ≥6, mean ≥4.0, no dimension below 3,
and no hard blocker.

## Hard blockers

- missing or unresolved target venue/profile;
- fabricated, duplicated, relabeled, or unsupported evidence;
- empirical headline claim with no executed public benchmark/data/task source;
- synthetic/generated evidence presented as the sole public validation;
- missing strongest relevant comparison for a comparative claim;
- claim contradicts raw evidence;
- uncertainty or repeatability omitted where required by the claim;
- stale or untraceable generated artifacts;
- unresolved citations or official-format violations;
- fewer than 35 verified BibTeX entries or 30 unique cited keys;
- self-drawn non-data figure where image-2 is required;
- private infrastructure/secrets leaked into rendered prose;
- paper value depends only on relabeling a weak result rather than a genuine
  insight.

A clean negative, null, diagnostic, or boundary result is not a hard blocker.

## Output contract

Return a compact simulated review:

```markdown
### Simulated peer review
- Venue: ...
- Contribution shape: method | systems | theory | diagnostic | evaluation |
  data | negative | boundary
- Decision: Accept | Reject
- Overall: N/10
- Scores: ...
- Strongest accept argument: ...
- Strongest reject argument: ...
- Blocking issues: ...
```

For `final_submission`, `done` is allowed only with `Decision: Accept`.
