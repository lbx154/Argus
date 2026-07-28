"""Shared role guidance for the project knowledge wiki."""

from __future__ import annotations

from pathlib import Path

from .auto_hooks import discover_wikis


def render_knowledge_wiki_block(
    project_root: Path | str,
    *,
    role: str,
) -> str:
    """Render the direct read/write contract for an initialized project wiki."""
    roots = discover_wikis(Path(project_root).expanduser())
    if not roots:
        return ""
    paths = "\n".join(f"- {path.resolve()}" for path in roots)
    reviewer_duty = (
        "As Reviewer, reconcile durable knowledge from the round before the final "
        "verdict: correct or refine relevant pages when the evidence changed them. "
        if role.lower() == "reviewer"
        else ""
    )
    return (
        "## Shared project knowledge wiki (direct read/write)\n"
        f"Role: {role}\n"
        "Wiki directories:\n"
        f"{paths}\n"
        "This is declarative knowledge: concepts, structures, mechanisms, "
        "principles, empirical facts, hypotheses, relationships, and "
        "contradictions (for example, Transformer architecture or why RL works). "
        "A Skill is procedural knowledge about how to perform work; do not copy "
        "procedures into the wiki. Events and CHECKPOINT.md hold history and "
        "current task state; do not copy round summaries or handoffs into the wiki.\n"
        "All Manager, Planner, Engineer, and Reviewer roles may directly read, "
        "create, and refine Markdown under `pages/`. The frontmatter `sources` "
        "field contains only paths relative to immutable `sources/`; cite project "
        "artifact paths in the page body. Preserve uncertainty and conflicting "
        "evidence, and retire "
        "obsolete pages reversibly under `pages/_retired/`. Do not emit proposed "
        "wiki operations in JSON. "
        f"{reviewer_duty}\n"
    )


__all__ = ["render_knowledge_wiki_block"]
