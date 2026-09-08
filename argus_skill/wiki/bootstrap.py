"""Initialize the minimal project Wiki tree."""
from __future__ import annotations

from pathlib import Path

_INDEX = "# Wiki Index\n\n_No Wiki pages yet._\n"
_README = """# Project Wiki

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
"""


def is_initialized_wiki(root: Path) -> bool:
    return (root / "pages").is_dir() and (root / "INDEX.md").is_file()


def init_wiki(project: str, *, base: Path | None = None) -> Path:
    value = str(project or "").strip().strip("/")
    if not value or ".." in Path(value).parts or Path(value).is_absolute():
        raise ValueError("project must be an explicit semantic path")
    base = base or Path.cwd()
    root = base / ".autors" / value / "wiki"
    (root / "pages").mkdir(parents=True, exist_ok=True)
    if not (root / "INDEX.md").exists():
        (root / "INDEX.md").write_text(_INDEX, encoding="utf-8")
    if not (root / "README.md").exists():
        (root / "README.md").write_text(_README, encoding="utf-8")
    return root


__all__ = ["init_wiki", "is_initialized_wiki"]
