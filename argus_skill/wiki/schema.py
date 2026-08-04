"""Minimal Wiki page format.

A Wiki page has exactly ``title`` and ``description`` frontmatter followed by
ordinary Markdown content.  Identity and hierarchy come from its Agent-authored
semantic path; there are no IDs, types, statuses, tags, run records, checksums,
or evaluator fields.

Writing is strict — :func:`serialize_page` only ever emits those two fields.
Reading is lenient: pages written before this format carry a richer frontmatter
(``id``/``type``/``status``/``tags``/``sources``/...) and no ``description`` at
all.  Rejecting them would silently empty every existing knowledge base, so
:func:`parse_page` ignores unknown keys and falls back to the first prose line
for a missing description.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml


@dataclass
class WikiPage:
    title: str
    description: str
    content: str


def serialize_page(page: WikiPage) -> str:
    front = yaml.safe_dump(
        {"title": page.title, "description": page.description},
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    return f"---\n{front}\n---\n\n{page.content.rstrip()}\n"


def _first_prose_line(content: str) -> str:
    """First prose line of ``content``, skipping headings and fenced code.

    Used as the description for legacy pages, which never carried one.
    """
    fenced = False
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not line or line.startswith("#"):
            continue
        return line
    return ""


def parse_page(text: str) -> WikiPage:
    """Parse the two-field page format for explicit Wiki tooling.

    Role Agents normally read files directly. This helper exists only for the
    optional Wiki index command and structural validation.

    Unknown frontmatter keys are ignored rather than rejected: every page
    written before the two-field format carries them, and failing the parse
    would drop those pages from the index entirely.
    """
    if not text.startswith("---\n"):
        raise ValueError("Wiki page must begin with YAML frontmatter")
    front, separator, content = text[4:].partition("\n---\n")
    if not separator:
        raise ValueError("Wiki page is missing its frontmatter terminator")
    loaded: Any = yaml.safe_load(front) or {}
    if not isinstance(loaded, dict):
        raise ValueError("Wiki frontmatter must be a mapping")
    title = loaded.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Wiki title must be a non-empty string")
    body = content.lstrip("\n").rstrip("\n")
    description = loaded.get("description")
    if not isinstance(description, str) or not description.strip():
        # Legacy page: derive one instead of discarding the page.
        description = _first_prose_line(body) or title.strip()
    return WikiPage(
        title=title.strip(),
        description=description.strip(),
        content=body,
    )


__all__ = ["WikiPage", "parse_page", "serialize_page"]
