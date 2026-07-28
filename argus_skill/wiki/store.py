"""File I/O for sources/ (immutable) and pages/ (mutable)."""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator, TypeVar

import yaml

from ..core.file_lock import exclusive_file_lock
from .schema import (
    PageCard,
    SourceNote,
    SourcePaper,
    SourceRepo,
    SourceRun,
    parse_frontmatter,
    serialize_frontmatter,
)

T = TypeVar("T", SourcePaper, SourceRepo, SourceRun, SourceNote)
log = logging.getLogger(__name__)
_WIKI_LOCK_TIMEOUT_SECONDS = 30.0
_WIKI_LOCK_POLL_SECONDS = 0.05

_SOURCE_SUBDIR = {
    SourcePaper: "papers",
    SourceRepo: "repos",
    SourceRun: "runs",
    SourceNote: "notes",
}
_PAGE_SUBDIR = {
    "concept": "concepts",
    "principle": "principles",
    "fact": "facts",
    "hypothesis": "hypotheses",
    "relationship": "relationships",
    "technique": "techniques",
    "conflict": "conflicts",
    "pattern": "patterns",
}


def wiki_root_for_project(project: str, *, base: Path | None = None) -> Path:
    base = base or Path.cwd()
    return base / ".autors" / project / "wiki"


def _validate_stem(stem: str) -> str:
    normalized = stem.strip().replace("/", "__")
    if (
        not normalized
        or "\\" in normalized
        or ".." in normalized
        or normalized.startswith(".")
    ):
        raise ValueError(f"invalid wiki id stem: {stem!r}")
    return normalized


def _stem_from_id(item_id: str) -> str:
    stem = item_id.split("/", 1)[1] if "/" in item_id else item_id
    return _validate_stem(stem)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


class WikiStore:
    def __init__(self, root: Path):
        self.root = root

    @contextmanager
    def _wiki_lock(self) -> Iterator[None]:
        lock_path = self.root / "data" / ".wiki.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as fh:
            with exclusive_file_lock(
                fh,
                timeout_seconds=_WIKI_LOCK_TIMEOUT_SECONDS,
                poll_seconds=_WIKI_LOCK_POLL_SECONDS,
                lock_name=f"wiki lock {lock_path}",
            ):
                yield

    # ---- sources ---------------------------------------------------------
    def write_source(self, src: SourcePaper | SourceRepo | SourceRun | SourceNote) -> Path:
        subdir = _SOURCE_SUBDIR[type(src)]
        stem = _stem_from_id(src.id)
        path = self.root / "sources" / subdir / f"{stem}.md"
        with self._wiki_lock():
            if path.exists():
                raise FileExistsError(f"sources are immutable: {path} already exists")
            _atomic_write_text(path, serialize_frontmatter(src))
        return path

    def read_source(self, cls: type[T], source_id: str) -> T:
        subdir = _SOURCE_SUBDIR[cls]
        stem = _stem_from_id(source_id)
        path = self.root / "sources" / subdir / f"{stem}.md"
        return parse_frontmatter(path.read_text(encoding="utf-8"), cls)

    # ---- pages -----------------------------------------------------------
    def write_page(self, card: PageCard) -> Path:
        subdir = _PAGE_SUBDIR[card.type]
        stem = _validate_stem(card.id)
        path = self.root / "pages" / subdir / f"{stem}.md"
        with self._wiki_lock():
            _atomic_write_text(path, serialize_frontmatter(card))
        return path

    def read_page(self, card_type: str, card_id: str) -> PageCard:
        subdir = _PAGE_SUBDIR[card_type]
        stem = _validate_stem(card_id)
        path = self.root / "pages" / subdir / f"{stem}.md"
        return parse_frontmatter(path.read_text(encoding="utf-8"), PageCard)

    def iter_pages(self, *, skip_invalid: bool = False) -> list[PageCard]:
        out: list[PageCard] = []
        pages_root = self.root / "pages"
        if not pages_root.exists():
            return out
        for md in sorted(pages_root.rglob("*.md")):
            # Retired pages are tombstoned under pages/_retired/ — out of
            # circulation and out of the derived indexes. Test the path RELATIVE
            # to pages/ so an unrelated ancestor dir named _retired can't hide
            # every page.
            if "_retired" in md.relative_to(pages_root).parts:
                continue
            try:
                out.append(parse_frontmatter(md.read_text(encoding="utf-8"), PageCard))
            except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
                if not skip_invalid:
                    raise
                log.warning("skipping invalid wiki page %s: %s", md, exc)
        return out

    def retire_page(
        self,
        card_type: str,
        card_id: str,
        *,
        reason: str,
        retired_by: str,
        today: "date | None" = None,
    ) -> Path:
        """Tombstone a page: move it out of circulation into ``pages/_retired/``
        with a retirement stamp, preserving the original card verbatim for audit
        and rollback. Pages are NEVER hard-deleted, and ``sources/`` (the immutable
        fact layer) is never touched. This is the wiki analogue of a skill archive.
        Raises ``FileNotFoundError`` if the page does not exist.
        """
        with self._wiki_lock():
            return self._retire_page_locked(
                card_type,
                card_id,
                reason=reason,
                retired_by=retired_by,
                today=today,
            )

    def retire_page_if_peer_active(
        self,
        card_type: str,
        card_id: str,
        *,
        peer_card_type: str,
        peer_card_id: str,
        reason: str,
        retired_by: str,
        today: "date | None" = None,
    ) -> Path | None:
        """Atomically retire a duplicate only while its chosen peer is active.

        Returns ``None`` for a benign concurrent race: the duplicate was already
        retired, or the representative disappeared and the compaction plan is no
        longer safe to apply.
        """
        peer_subdir = _PAGE_SUBDIR[peer_card_type]
        peer_stem = _validate_stem(peer_card_id)
        peer_path = self.root / "pages" / peer_subdir / f"{peer_stem}.md"
        subdir = _PAGE_SUBDIR[card_type]
        stem = _validate_stem(card_id)
        source_path = self.root / "pages" / subdir / f"{stem}.md"
        with self._wiki_lock():
            if not peer_path.exists() or not source_path.exists():
                return None
            return self._retire_page_locked(
                card_type,
                card_id,
                reason=reason,
                retired_by=retired_by,
                today=today,
            )

    def _retire_page_locked(
        self,
        card_type: str,
        card_id: str,
        *,
        reason: str,
        retired_by: str,
        today: "date | None",
    ) -> Path:
        subdir = _PAGE_SUBDIR[card_type]
        stem = _validate_stem(card_id)
        src = self.root / "pages" / subdir / f"{stem}.md"
        dest = self.root / "pages" / "_retired" / subdir / f"{stem}.md"
        stamp = (today or date.today()).isoformat()
        if not src.exists():
            raise FileNotFoundError(f"page not found: {src}")
        # Never overwrite an existing tombstone: if this stem was retired
        # before (re-created then re-retired), keep every retirement record.
        if dest.exists():
            for i in range(2, 100000):
                alt = dest.with_name(f"{stem}.{i}.md")
                if not alt.exists():
                    dest = alt
                    break
        original = src.read_text(encoding="utf-8").rstrip()
        tomb = (
            f"{original}\n\n"
            f"_RETIRED {stamp} by {retired_by}: "
            f"{(reason or '').strip() or '(no reason given)'}_\n"
        )
        _atomic_write_text(dest, tomb)
        src.unlink()
        return dest

    def iter_note_sources(self) -> list[SourceNote]:
        out: list[SourceNote] = []
        notes_root = self.root / "sources" / "notes"
        if not notes_root.exists():
            return out
        for md in sorted(notes_root.rglob("*.md")):
            out.append(parse_frontmatter(md.read_text(encoding="utf-8"), SourceNote))
        return out
