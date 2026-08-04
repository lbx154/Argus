"""No automatic migration into the minimal semantic Wiki."""
from __future__ import annotations

from pathlib import Path

from .store import WikiStore


def migrate_orphan_sources(store: WikiStore) -> list[Path]:
    _ = store
    return []


__all__ = ["migrate_orphan_sources"]
