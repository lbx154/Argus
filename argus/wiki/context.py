"""Path-only Wiki guidance shared by all roles."""
from __future__ import annotations

from pathlib import Path

from .auto_hooks import discover_wikis


def render_knowledge_wiki_block(project_root: Path | str, *, role: str) -> str:
    roots = discover_wikis(Path(project_root).expanduser())
    if not roots:
        return ""
    paths = "\n".join(f"- `{path.resolve()}`" for path in roots)
    return (
        "## Shared project Wiki\n"
        f"Role: {role}\n"
        "Wiki directories:\n"
        f"{paths}\n\n"
        "Search and read the Wiki yourself. Pages live under semantic paths in "
        "`pages/` and contain only `title`, `description`, and Markdown content. "
        "Use `INDEX.md` for progressive disclosure. When durable declarative "
        "knowledge changes, edit the relevant semantic page and INDEX directly. "
        "Project architecture and contracts, support/limitation matrices, stable "
        "environment constraints, and scope-qualified measurements belong here. "
        "An optional `## Insight` section may preserve useful interpretations "
        "separately from established facts. Link the motivating evidence, state "
        "scope and uncertainty, and connect relevant pages when transfer is "
        "plausible. Omit it when evidence is insufficient or it only repeats the "
        "summary. Search page bodies and linked Insight sections before reuse; "
        "recheck assumptions and revise interpretations as evidence changes. "
        "Procedures and checklists belong in Skills instead. Do not copy task "
        "history, handoffs, evaluator results, or runtime metadata into the Wiki."
    )


__all__ = ["render_knowledge_wiki_block"]
