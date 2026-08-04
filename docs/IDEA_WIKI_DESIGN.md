# Minimal semantic project Wiki

The Wiki is an Agent-readable filesystem, not a metadata database.

## Layout

```text
.autors/<project>/wiki/
├── INDEX.md
└── pages/
    └── <semantic path>.md
```

`INDEX.md` is the progressive-disclosure entrypoint. Agents maintain it directly
with semantic sections and links. The Harness does not generate, merge, score, or
rewrite Wiki content.

## Page format

```markdown
---
title: <title>
description: <one-line description>
---

# <title>

Markdown content.
```

No other frontmatter fields are allowed. In particular, pages do not contain IDs,
types, statuses, tags, source records, task/run references, timestamps, evaluator
results, or maturity scores. Identity comes from the Agent-authored semantic path.

## Role contract

Manager, Planner, Engineer, and Reviewer receive the Wiki path and independently
search/read it with file tools. When a mission yields durable declarative
knowledge, the Agent edits the relevant semantic page and `INDEX.md` before the
mission ends. Procedures belong in Skills; task state belongs in CHECKPOINT/events.
