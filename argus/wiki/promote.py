"""Copy reviewed project Wiki pages into the shared knowledge roots.

A page whose front matter carries ``audience: vertical`` is meant for every
later project of the same vertical; ``audience: global`` for every project on
the host. After a mission whose final review passed, the host copies such pages
from ``<workspace>/.autors/*/wiki/pages/`` into the matching shared Wiki under
the same relative path, and lists them in that Wiki's ``INDEX.md``.

Everything here is plain filesystem work. A page that cannot be read or parsed
is skipped, never raised; an older shared copy is only replaced when the
project's page is newer.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import yaml

from .auto_hooks import discover_wikis
from .schema import parse_page

log = logging.getLogger(__name__)

INDEX_FILENAME = "INDEX.md"
GLOBAL_DIRNAME = "_global"
SHARED_VERTICALS_DIRNAME = "_shared_verticals"
AUDIENCES = ("vertical", "global")


def _front_matter(text: str) -> dict[str, Any]:
    """The front matter mapping, or an empty dict when there is none."""
    if not text.startswith("---\n"):
        return {}
    front, separator, _content = text[4:].partition("\n---\n")
    if not separator:
        return {}
    loaded = yaml.safe_load(front)
    return loaded if isinstance(loaded, dict) else {}


def _page_audience(text: str) -> str:
    audience = _front_matter(text).get("audience")
    value = str(audience or "").strip().lower()
    return value if value in AUDIENCES else ""


def _safe_vertical(vertical: str) -> str:
    name = str(vertical or "").strip()
    if not name or name.startswith(".") or "/" in name or "\\" in name or "\x00" in name:
        return ""
    return name


def shared_target_root(shared_root: Path, *, audience: str, vertical: str) -> Path | None:
    """The shared Wiki a page of ``audience`` belongs to, or None when it has none."""
    if audience == "global":
        return shared_root / GLOBAL_DIRNAME
    if audience == "vertical":
        name = _safe_vertical(vertical)
        return shared_root / SHARED_VERTICALS_DIRNAME / name if name else None
    return None


def _index_heading(*, audience: str, vertical: str) -> str:
    if audience == "global":
        return "# Global knowledge\n"
    return f"# {vertical[:1].upper()}{vertical[1:]} knowledge\n"


def _append_index_line(target_root: Path, *, audience: str, vertical: str,
                       relative: str, title: str, description: str) -> None:
    """List the page in the shared INDEX.md once; create the file with a heading if missing."""
    index_path = target_root / INDEX_FILENAME
    try:
        existing = index_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = _index_heading(audience=audience, vertical=vertical) + "\n"
    if f"](pages/{relative})" in existing:
        return
    if existing and not existing.endswith("\n"):
        existing += "\n"
    line = f"- [{title}](pages/{relative}) — {description}\n"
    index_path.write_text(existing + line, encoding="utf-8")


def _candidate_pages(wiki_root: Path) -> list[tuple[Path, Path]]:
    """(source file, path relative to pages/) for every regular page under pages/."""
    pages_root = (wiki_root / "pages").resolve()
    found: list[tuple[Path, Path]] = []
    for path in sorted(pages_root.rglob("*.md")):
        relative = path.relative_to(pages_root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        try:
            if not path.is_file() or not path.resolve().is_relative_to(pages_root):
                continue
        except OSError:
            continue
        found.append((path, relative))
    return found


def _copy_if_newer(source: Path, destination: Path) -> bool:
    try:
        if destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime:
            return False
    except OSError:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def promote_wiki_pages(workspace: Path, *, vertical: str, shared_root: Path) -> dict[str, list[str]]:
    """Copy audience-tagged pages of every Wiki under ``workspace`` into ``shared_root``.

    Returns the pages copied per audience, as paths relative to ``pages/``.
    Untagged, unreadable or malformed pages are skipped; a shared copy that is
    at least as new as the project page is left alone. INDEX.md of the shared
    Wiki gains one line per newly listed page.
    """
    promoted: dict[str, list[str]] = {audience: [] for audience in AUDIENCES}
    try:
        wiki_roots = discover_wikis(Path(workspace))
    except OSError:
        log.exception("wiki promotion: could not list the project Wikis")
        return promoted
    for wiki_root in wiki_roots:
        for source, relative in _candidate_pages(wiki_root):
            try:
                text = source.read_text(encoding="utf-8")
                audience = _page_audience(text)
                if not audience:
                    continue
                page = parse_page(text)
            except (OSError, UnicodeError, ValueError, yaml.YAMLError):
                log.debug("wiki promotion: skipped unreadable page %s", source, exc_info=True)
                continue
            target_root = shared_target_root(shared_root, audience=audience, vertical=vertical)
            if target_root is None:
                continue
            rel_posix = relative.as_posix()
            try:
                if not _copy_if_newer(source, target_root / "pages" / relative):
                    continue
                _append_index_line(
                    target_root,
                    audience=audience,
                    vertical=vertical,
                    relative=rel_posix,
                    title=page.title,
                    description=page.description,
                )
            except OSError:
                log.exception("wiki promotion: could not copy %s into %s", source, target_root)
                continue
            promoted[audience].append(rel_posix)
    return promoted


__all__ = ["promote_wiki_pages", "shared_target_root"]
