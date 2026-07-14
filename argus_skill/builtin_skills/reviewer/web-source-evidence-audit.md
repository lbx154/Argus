---
name: Web-Source-Evidence-Audit
description: "Use when reviewing current web research, product behavior, implementation claims, launch posts, vendor benchmarks, public talks, source-code assertions, or closed-source uncertainty."
category: evidence-review
priority: high
version: 1
created_at: 2026-07-14T00:00:00+00:00
---

# Web Source-Evidence Audit

## Core principle

`SOURCE_EVIDENCE.json` validator PASS proves structural admissibility, not truth.
Audit whether each excerpt actually entails its claim and whether the source type
can support the claimed scope.

## Review sequence

1. Run:

   ```bash
   python -m argus_skill.verticals.research.source_evidence --project-root .
   ```

2. For every consequential claim, open its cited source or saved response and
   check URL, timezone-aware access date, retrieval method, verbatim excerpt,
   content hash, and source identity.
3. Apply both axes:
   - `public_fact`: source directly states or demonstrates it.
   - `reasonable_inference`: premises, confidence, and falsifier are explicit.
   - `unknown_closed_source`: search scope is recorded; no hidden detail is
     supplied from memory.
   - `public_implementation`: requires public code or reproducible observation.
   - `documented_behavior`: technical docs can support behavior, not undisclosed
     algorithms.
4. Compare research prose and design conclusions to the ledger. Labels in the
   artifact do not excuse stronger wording elsewhere.

## Marketing split

Preserve the vendor statement without upgrading its mechanism:

- Exact launch excerpt “the run iterates until answers converge”:
  `public_fact` + `vendor_claim` means the vendor demonstrably said it.
- “The runtime implements a defined convergence algorithm” remains
  `unknown_closed_source` + `unknown` without technical disclosure.

Likewise, “isolated environment” does not entail V8, QuickJS, process isolation,
prompt details, thresholds, or scoring logic.

## Verdict

- `continue`: missing source/access date, synthesized excerpt, invalid hash,
  broken reference, unsupported scope, inference presented as fact, or unknown
  internals asserted positively. Name the exact claim ID and required
  downgrade/refetch.
- `done`: mechanical validation passes, cited text entails every consequential
  claim, marketing remains vendor-attributed, inference remains labeled, and
  closed-source details remain unknown.

## Common mistakes

- Treating validator PASS as semantic certification.
- Rejecting “vendor said X” entirely instead of preserving it as
  `public_fact` + `vendor_claim`.
- Accepting a repository README as implementation source when the relevant code
  is absent.
