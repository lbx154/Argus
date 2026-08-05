# Minimal semantic Skill library

Agents receive Skill-library paths and discover relevant guidance with their own
file/search tools. There is no matcher, adapter, prompt injection, or runtime
parser.

## Skill format

```markdown
---
name: <semantic name>
description: <one-line description>
---

# <title>

Markdown guidance.
```

No other frontmatter fields are allowed. A Skill's identity is its Agent-authored
semantic path and name. The runtime does not generate names, IDs, numeric suffixes,
fingerprints, versions, reuse counters, or outcome labels.

A project library may contain an Agent-maintained `INDEX.md` for progressive
disclosure. Role-owned learning is written under `manager/`, `planner/`,
`engineer/`, or `reviewer/` in the project library. Agents update these semantic
paths directly; the Harness only supplies library roots and never reads, matches,
or mutates Skill content.
