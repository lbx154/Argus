# Project Knowledge Wiki

> Current Wiki/Skill boundary. See
> [`DESIGN_AUTHORITY.md`](DESIGN_AUTHORITY.md).

## Purpose

The project wiki is Argus's durable declarative knowledge layer. It preserves
knowledge that remains useful across missions:

- concepts and structures, such as Transformer architecture;
- mechanisms and principles, such as policy gradients and RL credit assignment;
- empirical facts and measured relationships;
- hypotheses with explicit uncertainty;
- relationships between concepts;
- contradictory evidence that has not yet been resolved.

It is not a mission journal and not a procedure library.

| Store | Owns |
|---|---|
| `events.jsonl` | What happened |
| `CHECKPOINT.md` / handoff | Current task state |
| Project wiki | What is known |
| Skills | How to perform reusable work |

## Storage

Each project owns one initialized tree:

```text
.autors/<project>/wiki/
├── sources/
│   ├── papers/
│   ├── repos/
│   └── notes/
├── pages/
│   ├── concepts/
│   ├── principles/
│   ├── facts/
│   ├── hypotheses/
│   ├── relationships/
│   ├── conflicts/
│   ├── techniques/        # legacy, readable during migration
│   ├── patterns/          # legacy, readable during migration
│   └── _retired/
├── queries/
├── data/
└── query_pack.md
```

`sources/` is immutable evidence. `pages/` is agent-authored synthesis.
`queries/` is a derived index and can always be rebuilt.

New wikis do not create `sources/runs/`. Existing run cards are legacy history
and are not injected into role prompts; canonical events and handoffs own that
information.

## Page contract

Pages are short Markdown documents with frontmatter:

```yaml
---
id: transformer-architecture
type: concept
status: candidate
title: Transformer architecture
tags: [transformer, attention]
sources:
  - papers/1706.03762.md
related_projects: []
revisit_after: null
created_at: 2026-07-28
last_reviewed_at: 2026-07-28
reviewer_note: "Cross-checked against the cited paper."
---
```

The body separates sourced facts, current synthesis, uncertainty, and known
conflicts. It cites immutable source IDs or real project artifacts. A page must
not contain a copied mission verdict, task status, handoff, or step-by-step
workflow.

Statuses describe page maturity, not the result of one observation or check:

- `scratch`: useful claim noticed, still weakly organized or sourced;
- `candidate`: coherent and supported enough to use with care;
- `stable`: repeatedly checked and safe to rely on.

Writers must use exactly those three values. Evidence words such as `observed`
and `verified` belong in the body, source metadata, or reviewer note. The reader
conservatively maps legacy `observed` pages to `scratch` and legacy `verified`
pages to `candidate`, so an older producer cannot make durable knowledge vanish
or silently promote a single check to `stable`.

## Ownership

Manager, Planner, Engineer, and Reviewer all directly read and write the same
wiki pages with ordinary file tools. There is no structured page-operation
channel and no single-writer router.

Reviewer has an additional runtime responsibility: when a reviewed round changes
durable knowledge, Reviewer reconciles the relevant pages against the real
evidence before returning the verdict. This does not require manufacturing a
page every round.

Retirement is reversible: move obsolete pages under `pages/_retired/` with a
reason. Never erase immutable evidence.

## Runtime support

The harness remains a domain-neutral pipe:

- bootstrap the tree;
- import already-produced bibliography/literature artifacts as sources;
- rebuild derived indexes;
- compact old tombstones.

The harness does not decide what a fact means, create placeholder knowledge
pages, promote claims based on string matching, or copy round history. Semantic
judgment belongs to the roles.
