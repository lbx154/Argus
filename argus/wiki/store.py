"""Minimal semantic-path Wiki storage."""
from __future__ import annotations

from pathlib import Path

from ..core.atomic_replace import write_atomic
from ..core.file_lock import exclusive_file_lock
from .schema import WikiPage, serialize_page

# Wiki writes are atomic; this only bounds lock handoff, not the write's work.
_WIKI_LOCK_TIMEOUT_SECONDS = 30.0
_WIKI_LOCK_POLL_SECONDS = 0.05


def _atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text, text_mode=True)


def atomic_write_bytes(path: Path, data: bytes | str, *, text_mode: bool = False) -> None:
    """Replace ``path`` atomically; bytes are restored exactly as given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if text_mode:
        write_atomic(path, str(data))
    else:
        write_atomic(path, data if isinstance(data, bytes) else str(data).encode("utf-8"))


def _semantic_page_path(root: Path, value: str | Path) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        candidate = relative.resolve()
    else:
        candidate = (root / "pages" / relative).resolve()
    pages_root = (root / "pages").resolve()
    try:
        candidate.relative_to(pages_root)
    except ValueError as exc:
        raise ValueError("Wiki page path must stay under pages/") from exc
    if candidate.suffix.casefold() != ".md":
        raise ValueError("Wiki page path must end in .md")
    if any(part.startswith(".") or part in {"..", "_retired"} for part in relative.parts):
        raise ValueError("Wiki page path must be an explicit semantic path")
    return candidate


class WikiStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "pages").mkdir(parents=True, exist_ok=True)

    def _locked(self):
        lock = self.root / ".wiki.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        handle = lock.open("a+", encoding="utf-8")
        return handle, exclusive_file_lock(
            handle,
            timeout_seconds=_WIKI_LOCK_TIMEOUT_SECONDS,
            poll_seconds=_WIKI_LOCK_POLL_SECONDS,
            lock_name=f"wiki lock {lock}",
        )

    def write_page(self, semantic_path: str | Path, page: WikiPage) -> Path:
        path = _semantic_page_path(self.root, semantic_path)
        handle, lock = self._locked()
        try:
            with lock:
                _atomic_write_text(path, serialize_page(page))
        finally:
            handle.close()
        return path


__all__ = ["WikiStore", "_atomic_write_text", "atomic_write_bytes"]
