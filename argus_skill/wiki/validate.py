"""Schema and link-integrity validation for a wiki tree."""
from __future__ import annotations

from .store import WikiStore


class ValidationError(Exception):
    pass


def validate_wiki(store: WikiStore) -> None:
    sources_root = store.root / "sources"
    for card in store.iter_pages():
        for ref in card.sources:
            # ref is e.g. "papers/2406.12345.md"; resolve under sources/.
            target = sources_root / ref
            if not target.exists():
                raise ValidationError(
                    f"dangling source ref in {card.id}: {ref} -> {target} missing"
                )
