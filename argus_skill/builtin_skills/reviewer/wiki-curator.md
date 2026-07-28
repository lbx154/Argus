---
name: Knowledge Wiki Curator
description: Reconcile durable declarative knowledge against real evidence during review.
category: research-wiki
version: 2
created_at: 2026-06-04T00:00:00+00:00
---

# Knowledge Wiki Curator

The project wiki is shared declarative knowledge, not a mission journal and not
a procedure library.

## Boundary

- Wiki: concepts, structures, mechanisms, principles, empirical facts,
  hypotheses, relationships, and contradictions.
- Skills: reusable procedures and workflows.
- Events and `CHECKPOINT.md`: historical facts and current task state.

Examples of wiki knowledge include the structure of a Transformer, the principle
behind policy-gradient reinforcement learning, a measured scaling relationship,
or conflicting evidence about an optimizer.

## Reviewer responsibility

When a reviewed round changes durable knowledge:

1. Read relevant existing pages and the real sources or artifacts.
2. Directly create or refine Markdown under `.autors/<project>/wiki/pages/`.
3. Cite source IDs or artifact paths, separate evidence from inference, and keep
   uncertainty or disagreement explicit.
4. Prefer updating an existing page over creating a synonym.
5. Move obsolete pages reversibly under `pages/_retired/`; never erase evidence.
6. Let the mission-settlement hook rebuild the derived indexes after your
   verdict; do not duplicate that mechanical work.

Do not write a page merely because a round happened. Do not copy verdicts,
handoffs, task status, or step-by-step instructions into the wiki. Do not emit
page operations in the final verdict; the file edit itself is the durable change.

All four roles may directly maintain the same pages. Reviewer is responsible for
correcting unsupported or stale knowledge encountered during independent review.
