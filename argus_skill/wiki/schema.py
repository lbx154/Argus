"""Minimal Wiki page format.

A Wiki page has exactly ``title`` and ``description`` frontmatter followed by
ordinary Markdown content.  Identity and hierarchy come from its Agent-authored
semantic path; there are no IDs, types, statuses, tags, run records, checksums,
or evaluator fields.
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


def parse_page(text: str) -> WikiPage:
    """Parse the two-field page format for explicit Wiki tooling.

    Role Agents normally read files directly. This helper exists only for the
    optional Wiki index command and structural validation.
    """
    if not text.startswith("---\n"):
        raise ValueError("Wiki page must begin with YAML frontmatter")
    front, separator, content = text[4:].partition("\n---\n")
    if not separator:
        raise ValueError("Wiki page is missing its frontmatter terminator")
    loaded: Any = yaml.safe_load(front) or {}
    if not isinstance(loaded, dict):
        raise ValueError("Wiki frontmatter must be a mapping")
    if set(loaded) != {"title", "description"}:
        raise ValueError("Wiki frontmatter allows only title and description")
    title = loaded.get("title")
    description = loaded.get("description")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Wiki title must be a non-empty string")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Wiki description must be a non-empty string")
    return WikiPage(
        title=title.strip(),
        description=description.strip(),
        content=content.lstrip("\n").rstrip("\n"),
    )


__all__ = ["WikiPage", "parse_page", "serialize_page"]
