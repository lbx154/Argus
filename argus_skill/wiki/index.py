"""Optional rebuild of the single human-readable Wiki INDEX.md."""
from __future__ import annotations

from datetime import date

from .store import WikiStore, _atomic_write_text


def rebuild_indexes(store: WikiStore, *, today: date | None = None) -> None:
    """Write one flat semantic index.

    Normal missions let Agents maintain INDEX.md directly. This explicit helper
    is retained for repair tooling and only reads the minimal two-field format.
    """
    _ = today
    lines = ["# Wiki Index\n\n"]
    rows = store.iter_pages(skip_invalid=True)
    for path, page in rows:
        relative = path.relative_to(store.root).as_posix()
        lines.append(f"- [{page.title}]({relative}) — {page.description}\n")
    if not rows:
        lines.append("_No Wiki pages yet._\n")
    _atomic_write_text(store.root / "INDEX.md", "".join(lines))


__all__ = ["rebuild_indexes"]
