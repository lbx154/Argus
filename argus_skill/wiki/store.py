"""File I/O for sources/ (immutable) and pages/ (mutable)."""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TypeVar

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

_SOURCE_SUBDIR = {
    SourcePaper: "papers",
    SourceRepo: "repos",
    SourceRun: "runs",
    SourceNote: "notes",
}
_PAGE_SUBDIR = {
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
        import fcntl

        lock_path = self.root / "data" / ".wiki.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

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

    def iter_pages(self) -> list[PageCard]:
        out: list[PageCard] = []
        pages_root = self.root / "pages"
        if not pages_root.exists():
            return out
        for md in sorted(pages_root.rglob("*.md")):
            out.append(parse_frontmatter(md.read_text(encoding="utf-8"), PageCard))
        return out

    def iter_run_sources(self) -> list[SourceRun]:
        out: list[SourceRun] = []
        runs_root = self.root / "sources" / "runs"
        if not runs_root.exists():
            return out
        for md in sorted(runs_root.rglob("*.md")):
            out.append(parse_frontmatter(md.read_text(encoding="utf-8"), SourceRun))
        return out

    def iter_note_sources(self) -> list[SourceNote]:
        out: list[SourceNote] = []
        notes_root = self.root / "sources" / "notes"
        if not notes_root.exists():
            return out
        for md in sorted(notes_root.rglob("*.md")):
            out.append(parse_frontmatter(md.read_text(encoding="utf-8"), SourceNote))
        return out
