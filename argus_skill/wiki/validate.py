"""Validation for the minimal Wiki tree."""
from __future__ import annotations

from .bootstrap import is_initialized_wiki
from .store import WikiStore


class ValidationError(Exception):
    pass


def validate_wiki_structure(store: WikiStore) -> None:
    if not is_initialized_wiki(store.root):
        raise ValidationError("Wiki requires pages/ and INDEX.md")
