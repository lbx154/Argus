---
name: "Web-Primary-Source-Evidence"
description: "Use when researching current software, products, APIs, agent behavior, implementation details, vendor benchmarks, launch claims, public talks, or closed-source systems from web sources."
---

# Web Primary-Source Evidence

## Core principle

Classify the **claim**, then record what each source can actually prove. A
source being public or official does not make every statement in it an
implementation fact.

## Required artifact

Write `research/SOURCE_EVIDENCE.json` with `version`, `sources[]`, and
`claims[]`. Run:

```bash
python -m argus_skill.verticals.research.source_evidence --project-root .
```

The validator checks shape and admissibility only. The Reviewer judges whether
the quoted evidence really entails the claim.

## Source axis

- Implementation: `public_source_code` or `reproducible_observation`.
- Documented behavior: `official_technical_docs`, `official_changelog`, or
  `official_technical_talk`.
- Statements/leads: `vendor_marketing` proves only “vendor claims X”;
  `third_party` is not first-party implementation evidence.

Every source records: `source_id`, canonical `url`, `title`, `publisher`,
`source_type`, `published_at`, `updated_at`, timezone-aware `accessed_at`,
`retrieval_method`, a short `supporting_excerpt`, and `content_hash`.
`supporting_excerpt` is verbatim source text or verbatim command output, never
your bracketed interpretation. `content_hash` is exactly one
`sha256:<64 lowercase hex>` digest of the saved response or exact excerpt.

## Claim axis

- `public_fact`: directly supported by cited source text/code/observation.
- `reasonable_inference`: cite `premise_claim_ids`, confidence, and a concrete
  falsifier. Never relabel the inference as a fact.
- `unknown_closed_source`: state the exact unknown and `search_scope`. Absence
  of public evidence does not reveal the hidden implementation.

Claim `scope` is one of `documented_behavior`, `public_implementation`,
`measured_behavior`, `vendor_claim`, or `unknown`.

When a launch page says “the system iterates until convergence,” split the
statement from the mechanism:

- “The vendor says it iterates until convergence” =
  `public_fact` + `vendor_claim`, with the verbatim marketing excerpt.
- “The implementation has a defined convergence algorithm” =
  `unknown_closed_source` + `unknown` until technical evidence discloses it.

## Evidence rules

1. Fetch before citing; authority, snippets, and model memory are not evidence.
2. A claim needs a verbatim excerpt from its cited source.
3. `public_implementation` requires code or reproducible observation. Marketing
   and case studies remain `vendor_claim`.
4. Repository absence is a `reproducible_observation`: quote the actual tree/API
   output and classify hidden internals as unknown.

## Minimal example

```json
{
  "version": 1,
  "sources": [{
    "source_id": "workflow-docs",
    "url": "https://example.com/technical-docs",
    "title": "Workflow documentation",
    "publisher": "Vendor",
    "source_type": "official_technical_docs",
    "published_at": "",
    "updated_at": "",
    "accessed_at": "2026-07-14T12:00:00+00:00",
    "retrieval_method": "web_fetch",
    "supporting_excerpt": "The runtime executes the script in isolation.",
    "content_hash": "sha256:41d089dfae906ecac5aa7350991aa18024e7fc707b6b1494a75b0dcc592ad742"
  }],
  "claims": [{
    "claim_id": "isolated-runtime",
    "text": "The documented runtime is isolated.",
    "classification": "public_fact",
    "scope": "documented_behavior",
    "source_ids": ["workflow-docs"],
    "premise_claim_ids": [],
    "confidence": "",
    "falsifier": "",
    "search_scope": ""
  }]
}
```

## Common mistakes

- “Official” ⇒ implementation: wrong; inspect claim scope.
- Bracketed conclusions in excerpts: save verbatim tree/source output.
- Multiple joined hashes or missing access date: provenance is invalid.
