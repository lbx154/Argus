"""Minimal semantic-path Wiki storage."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Iterator

from ..core.file_lock import exclusive_file_lock
from .schema import WikiPage, parse_page, serialize_page

_WIKI_LOCK_TIMEOUT_SECONDS = 30.0
_WIKI_LOCK_POLL_SECONDS = 0.05


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.writing-{os.getpid()}-{threading.get_ident()}"
    )
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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

    def read_page(self, semantic_path: str | Path) -> WikiPage:
        path = _semantic_page_path(self.root, semantic_path)
        return parse_page(path.read_text(encoding="utf-8"))

    def iter_page_paths(self) -> Iterator[Path]:
        pages = self.root / "pages"
        if not pages.exists():
            return
        for path in sorted(pages.rglob("*.md")):
            if any(part.startswith(".") for part in path.relative_to(pages).parts):
                continue
            yield path

    def iter_pages(self, *, skip_invalid: bool = False) -> list[tuple[Path, WikiPage]]:
        rows: list[tuple[Path, WikiPage]] = []
        for path in self.iter_page_paths():
            try:
                rows.append((path, parse_page(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                if not skip_invalid:
                    raise
        return rows


__all__ = ["WikiStore", "_atomic_write_text"]
