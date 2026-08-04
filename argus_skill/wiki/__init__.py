"""Minimal per-project Wiki: semantic pages plus one INDEX.md."""
from __future__ import annotations

from .schema import WikiPage, parse_page, serialize_page
from .store import WikiStore

__all__ = ["WikiPage", "WikiStore", "parse_page", "serialize_page"]
