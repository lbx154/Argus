"""Schema and link-integrity validation for a wiki tree."""
from __future__ import annotations

from .bootstrap import is_initialized_wiki
from .store import WikiStore


class ValidationError(Exception):
    pass


def validate_wiki_structure(store: WikiStore) -> None:
    if is_initialized_wiki(store.root):
        return
    required_dirs = (
        "sources",
        "sources/papers",
        "sources/repos",
        "sources/runs",
        "pages",
        "pages/techniques",
        "pages/conflicts",
        "pages/patterns",
        "queries",
        "data",
    )
    required_files = ("data/schema.yaml", "query_pack.md")
    missing = [
        rel for rel in required_dirs if not (store.root / rel).is_dir()
    ] + [
        rel for rel in required_files if not (store.root / rel).is_file()
    ]
    raise ValidationError("missing wiki structure: " + ", ".join(missing))


def validate_wiki(store: WikiStore) -> None:
    validate_wiki_structure(store)
    sources_root = store.root / "sources"
    for card in store.iter_pages():
        for ref in card.sources:
            # ref is e.g. "papers/2406.12345.md"; resolve under sources/.
            target = sources_root / ref
            if not target.exists():
                raise ValidationError(
                    f"dangling source ref in {card.id}: {ref} -> {target} missing"
                )
