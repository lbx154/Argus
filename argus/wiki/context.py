"""Path-only Wiki guidance shared by all roles."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from ..core import paths
from .auto_hooks import discover_wikis

log = logging.getLogger(__name__)


def shared_knowledge_roots(project_root: Path | str, *, global_root: Path | str | None = None) -> list[Path]:
    """Shared Wiki roots this project reads: its vertical's first, then the global one.

    A root counts only when its ``pages/`` directory exists. The vertical comes
    from the Manager-persisted project state; a project without a decided
    vertical, or one whose state cannot be read, still sees the global root.
    """
    from ..skills.vertical_select import resolve_skill_scope

    try:
        vertical = resolve_skill_scope(Path(project_root).expanduser())
    except Exception:  # noqa: BLE001 - an unreadable state is not the prompt's problem
        vertical = ""
    candidates: list[Path] = []
    try:
        if vertical:
            candidates.append(paths.shared_vertical_wiki_root(vertical, global_root))
        candidates.append(paths.global_wiki_root(global_root))
    except ValueError:
        log.debug("wiki context: shared knowledge roots could not be resolved", exc_info=True)
        return []
    roots: list[Path] = []
    for root in candidates:
        try:
            if (root / "pages").is_dir():
                roots.append(root)
        except OSError:
            continue
    return roots


def render_knowledge_wiki_block(
    project_root: Path | str,
    *,
    role: str,
    shared_roots: Sequence[Path] = (),
) -> str:
    roots = discover_wikis(Path(project_root).expanduser())
    if not roots:
        return ""
    paths_text = "\n".join(f"- `{path.resolve()}`" for path in roots)
    shared = ""
    if shared_roots:
        shared_lines = "\n".join(f"- `{Path(path).resolve()}`" for path in shared_roots)
        shared = (
            "\n\nShared knowledge (read before deciding; written by the host after review):\n"
            f"{shared_lines}\n\n"
            "A page useful beyond this project carries `audience: vertical` (or "
            "`audience: global`) in its front matter; after a passing review the host "
            "copies it into the shared knowledge of this vertical, which later projects read."
        )
    return (
        "## Shared project Wiki\n"
        f"Role: {role}\n"
        "Wiki directories:\n"
        f"{paths_text}\n\n"
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
        + shared
    )


PRINCIPLES_HEADER = (
    "## How this vertical works now (principles distilled from earlier missions)"
)
PRINCIPLES_CHAR_LIMIT = 1500
_PRINCIPLES_FILENAME = "principles.md"


def _strip_front_matter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    _front, separator, content = text[4:].partition("\n---\n")
    return content if separator else text


def _without_history(body: str) -> str:
    """The principles themselves: everything before a ``## History`` heading."""
    kept: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and stripped[3:].strip().lower() == "history":
            break
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def render_principles_block(vertical_root: Path | str, *, limit: int = PRINCIPLES_CHAR_LIMIT) -> str:
    """The vertical's ``principles.md`` as one bounded prompt block, or ``""``.

    The front matter and the ``## History`` section are dropped; what remains
    is the numbered list of working rules with their evidence links. The block
    never exceeds ``limit`` characters, header included.
    """
    path = Path(vertical_root).expanduser() / _PRINCIPLES_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    body = _without_history(_strip_front_matter(text))
    if not body:
        return ""
    budget = limit - len(PRINCIPLES_HEADER) - 1
    if budget <= 0:
        return ""
    if len(body) > budget:
        cut = body[: budget - 1]
        newline = cut.rfind("\n")
        if newline > budget // 2:
            cut = cut[:newline]
        body = cut.rstrip() + "…"
    return f"{PRINCIPLES_HEADER}\n{body}"


def render_project_principles(
    project_root: Path | str, *, global_root: Path | str | None = None,
) -> str:
    """The principles block of the vertical this project was routed to, or ``""``.

    The vertical is read the same way :func:`shared_knowledge_roots` reads it;
    a project without a decided vertical, or one whose state cannot be read,
    gets no block. Nothing here raises into a prompt builder.
    """
    from ..skills.vertical_select import resolve_skill_scope

    try:
        vertical = resolve_skill_scope(Path(project_root).expanduser())
    except Exception:  # noqa: BLE001 - an unreadable state is not the prompt's problem
        return ""
    if not vertical:
        return ""
    try:
        root = paths.shared_vertical_wiki_root(vertical, global_root)
    except ValueError:
        return ""
    return render_principles_block(root)


__all__ = [
    "PRINCIPLES_HEADER",
    "render_knowledge_wiki_block",
    "render_principles_block",
    "render_project_principles",
    "shared_knowledge_roots",
]
