"""File I/O for sources/ (immutable) and pages/ (mutable)."""
from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from .schema import (
    PageCard,
    SourcePaper,
    SourceRepo,
    SourceRun,
    parse_frontmatter,
    serialize_frontmatter,
)

T = TypeVar("T", SourcePaper, SourceRepo, SourceRun)

_SOURCE_SUBDIR = {
    SourcePaper: "papers",
    SourceRepo: "repos",
    SourceRun: "runs",
}
_PAGE_SUBDIR = {
    "technique": "techniques",
    "conflict": "conflicts",
    "pattern": "patterns",
}


def wiki_root_for_project(project: str, *, base: Path | None = None) -> Path:
    base = base or Path.cwd()
    return base / ".autors" / project / "wiki"


class WikiStore:
    def __init__(self, root: Path):
        self.root = root

    # ---- sources ---------------------------------------------------------
    def write_source(self, src: SourcePaper | SourceRepo | SourceRun) -> Path:
        subdir = _SOURCE_SUBDIR[type(src)]
        # src.id is e.g. "papers/2406.12345"; strip prefix to get filename stem.
        stem = src.id.split("/", 1)[1] if "/" in src.id else src.id
        path = self.root / "sources" / subdir / f"{stem}.md"
        if path.exists():
            raise FileExistsError(f"sources are immutable: {path} already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialize_frontmatter(src), encoding="utf-8")
        return path

    def read_source(self, cls: type[T], source_id: str) -> T:
        subdir = _SOURCE_SUBDIR[cls]
        stem = source_id.split("/", 1)[1] if "/" in source_id else source_id
        path = self.root / "sources" / subdir / f"{stem}.md"
        return parse_frontmatter(path.read_text(encoding="utf-8"), cls)

    # ---- pages -----------------------------------------------------------
    def write_page(self, card: PageCard) -> Path:
        subdir = _PAGE_SUBDIR[card.type]
        path = self.root / "pages" / subdir / f"{card.id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialize_frontmatter(card), encoding="utf-8")
        return path

    def read_page(self, card_type: str, card_id: str) -> PageCard:
        subdir = _PAGE_SUBDIR[card_type]
        path = self.root / "pages" / subdir / f"{card_id}.md"
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
