---
name: Research Submission Assurance Gate
description: Decide whether an AI research paper is submission-ready for its selected current venue using public evidence, claim alignment, venue-aware format checks, image-2 figure provenance, and strict L2 review.
category: research-audit
version: 2
created_at: 2026-07-17T00:00:00+00:00
---

# Research Submission Assurance Gate

## Purpose

Run the final high-strictness gate before calling a paper submission-ready. The
gate is venue-aware and contribution-neutral: positive, negative, diagnostic,
characterization, evaluation, systems, theory, data, and boundary papers may all
pass when they have research value and evidence appropriate to their claims.

## Completion authority

The L2 Reviewer certifies `final_submission` against the full pipeline
checklist. Validation tools provide evidence; they do not manufacture a quality
verdict.

## Required inputs

- `research/PIPELINE_STATE.json`;
- `research/VENUE_SELECTION.md` and a loadable
  `research/VENUE_PROFILE.json`;
- official venue/template sources with a deadline that was open when selected;
- canonical literature grounding;
- experiment/proof manifests and raw evidence;
- public benchmark/data provenance for empirical claims;
- claims-evidence mapping and artifact manifest/freshness;
- manuscript source, PDF, build log, figures, tables, and bibliography;
- format, academic-language, infrastructure, and layout review artifacts;
- the actual figure files included by the manuscript;
- `paper/SUBMISSION_ASSURANCE.md` and `.json`.

## Assurance layers

Use `PASS | WARN | FAIL | BLOCKED | ERROR | NOT_APPLICABLE`.

### 1. Venue selection and format

- Verify the selected venue is appropriate to the domain.
- Verify the selection record used official CCF classification, CFP/deadline,
  and author-kit sources.
- Apply the selected profile's current page/word limit, anonymity, required
  sections, bibliography rules, style files, and forbidden packages.
- Never substitute historical EMNLP or AAAI rules for an unresolved venue.

### 2. Research value

- Identify the contribution shape.
- Confirm the paper changes understanding or practice through a method, system,
  theorem, reliable diagnosis, characterization, evaluation capability, data
  resource, negative result, or boundary condition.
- Do not require a positive metric improvement.

### 3. Public evidence integrity

- Every final empirical claim must include executed evidence from at least one
  appropriate public benchmark, dataset, task suite, challenge, or official
  evaluation release.
- Record official source, version, split/cohort, license/access, evaluation unit,
  metric/evaluator, filtering, and claim tested.
- Synthetic/generated diagnostics may supplement but must not be the sole final
  empirical evidence or be presented as public data.
- Evidence scale is claim-proportional. Do not impose a universal benchmark,
  task, model, seed, or condition count.
- Reject fabricated rows, duplicate/relabelled examples, silent exclusions, and
  benchmark construction presented as execution.

### 4. Comparison, uncertainty, and result honesty

- Include the strongest relevant comparisons required to interpret the claim.
- Verify controls/ablations isolate the claimed factor.
- Use uncertainty, repeatability, sensitivity, formal guarantees, or other
  domain-appropriate support.
- Preserve losses, nulls, contradictions, and failed cases.
- A valid negative or boundary result may pass when its research value is clear.

### 5. Claim and artifact audit

- Map every material claim to raw evidence.
- Regenerate paper numbers, tables, and data figures from canonical sources.
- Verify artifact hashes, schemas, source links, and downstream freshness.
- Block unsupported SOTA/generalization/causal language and stale generated
  copies.

### 6. Literature and citation audit

- Cover material premises, nearest competitors, foundations, contradictions,
  and the frontier with verified primary sources.
- Require verified, claim-complete bibliography coverage appropriate to the
  paper; do not impose a universal entry count.
- Block invented citations, missing bibliography entries, citation dumping, and
  metadata contradictions.

### 7. Figure quality

- Reviewer inspects the actual rendered figures for readability, factual
  correctness, coherence, and good-enough venue quality.
- Image-2 is optional and its absence is not a submission blocker.
- Minor stylistic imperfections, optional metadata gaps, and preference-level
  differences do not block submission or trigger repeated figure regeneration.

### 8. Academic language and paper infrastructure

- The paper must explain the evaluated research system/method, public evidence,
  comparisons, metrics, uncertainty, and scope.
- Remove private paths, secrets, authoring routes, and Argus/Codex internals.
- Preserve legitimate scientific environment details needed for systems,
  efficiency, or reproducibility claims.
- Review artifacts are evidence; fix sources and rerun them rather than editing
  PASS fields.

### 9. Layout and package

- Compile cleanly under the selected official template.
- Respect the selected venue's page/word/section/bibliography rules.
- Ensure readable figures/tables/captions and no unresolved references,
  placeholders, or serious overfull/overlap defects.
- Verify submission copies are fresh and included in the artifact manifest.

## Final verdict

`PASS` requires:

- current, explicit venue selection/profile;
- authentic public evidence for empirical claims;
- claim-proportional scale and uncertainty;
- strongest relevant comparisons;
- verified, claim-complete bibliography coverage;
- one defensible publication thesis; negative/boundary framing passes only when
  it carries standalone insight and implementation inadequacy has been ruled out;
- current manuscript, reviews, figures, and package;
- full-pipeline L2 Reviewer certification.

`WARN` is only for non-blocking caveats. Missing public evidence, unresolved
venue, unsupported claims, fabricated provenance, or failed required reviews are
`FAIL`/`BLOCKED`, never `WARN`.

## Output

Write:

- `paper/SUBMISSION_ASSURANCE.md`;
- `paper/SUBMISSION_ASSURANCE.json`.

The JSON should include:

```json
{
  "verdict": "FAIL",
  "target_venue": "",
  "contribution_shape": "",
  "blocking_issues": [],
  "layers": {
    "venue": {"verdict": "BLOCKED", "evidence": []},
    "research_value": {"verdict": "WARN", "evidence": []},
    "public_evidence": {"verdict": "FAIL", "evidence": []},
    "claim_alignment": {"verdict": "FAIL", "evidence": []},
    "literature_citations": {"verdict": "WARN", "evidence": []},
    "image2_figures": {"verdict": "FAIL", "evidence": []},
    "language_infrastructure": {"verdict": "FAIL", "evidence": []},
    "layout_package": {"verdict": "FAIL", "evidence": []}
  }
}
```
