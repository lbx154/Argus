# Project Wiki

The Wiki contains declarative knowledge authored by Agents.

- `pages/` contains semantically named Markdown pages.
- `INDEX.md` links pages by meaning and gives a one-line description.

Each page has exactly this format:

```markdown
---
title: <title>
description: <one-line description>
---

# <title>

Source-backed factual content.
```

## Optional Insight

A page may add a `## Insight` section after its factual content when evidence
supports a useful interpretation, abstraction, transferable lesson, or hypothesis.
Label it as interpretation rather than established fact. Omit the entire section
when there is no well-grounded insight; do not leave an empty heading or repeat
the summary in speculative language.

In ordinary prose, connect the inference to specific observations and their
sources, explain where it may apply or fail, and state assumptions or possible
counterexamples. These are quality guides, not required fields. Use separate
subheadings for independently useful insights. Link relevant Wiki pages or
accessible project evidence when suggesting transfer; a connection is not proof
that the conclusion generalizes.

Search page bodies as well as `INDEX.md` when reusing knowledge. A link to
`pages/<semantic-path>.md#insight` can cite the interpretation separately from
its supporting facts. Revise or withdraw it when evidence changes, citing the
new evidence without erasing still-valid observations. Keep the page description
and INDEX wording clear when they describe a hypothesis rather than a fact.
