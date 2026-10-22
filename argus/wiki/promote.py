"""Copy reviewed project Wiki pages into the shared knowledge roots.

A page whose front matter carries ``audience: vertical`` is meant for every
later project of the same vertical -- the project's own, or the one its
``vertical:`` names when this host knows it; ``audience: global`` for every
project on the host. After a mission whose final review passed, the host
copies such pages from ``<workspace>/.autors/*/wiki/pages/`` into the matching
shared Wiki under the same relative path, with the files of its Wiki it links
to (a small check under ``checks/``), and lists them in that Wiki's
``INDEX.md``.

Everything here is plain filesystem work. A page that cannot be read or parsed
is skipped, never raised; an older shared copy is only replaced when the
project's page is newer, and a shared copy retired after the project's page
last changed stays retired.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .auto_hooks import discover_wikis
from .correct import HISTORY_DIRNAME, HISTORY_STAMP
from .schema import parse_page

log = logging.getLogger(__name__)

INDEX_FILENAME = "INDEX.md"
GLOBAL_DIRNAME = "_global"
SHARED_VERTICALS_DIRNAME = "_shared_verticals"
AUDIENCES = ("vertical", "global")
_LINK_RE = re.compile(r"\]\(([^)\s#]+)")


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


def declared_vertical(text: str, default: str) -> str:
    """The vertical a page's or Skill's front matter names, when this host knows it; else ``default``.

    The writer knows where a finding belongs; the host only checks that the
    vertical exists.
    """
    from ..skills.vertical_select import require_vertical

    try:
        named = _front_matter(text).get("vertical")
        return require_vertical(named) if named else default
    except Exception:  # noqa: BLE001 - an unknown or unreadable name keeps the default
        return default


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


def linked_files(page: Path, text: str, library: Path) -> list[Path]:
    """Files other than pages under ``library`` that ``page`` links to: the code it carries.

    A link out of the library points at the material the page cites, not at
    the page's own code, and is left out.
    """
    library = Path(os.path.normpath(library.absolute()))
    found: list[Path] = []
    for target in _LINK_RE.findall(text):
        path = Path(os.path.normpath(page.absolute().parent / target))
        if ("://" not in target and path.suffix.casefold() != ".md"
                and path.is_relative_to(library) and path not in found):
            found.append(path)
    return found


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


def _retired_since(source: Path, target_root: Path, relative: Path) -> bool:
    """Whether the shared copy is gone and its library kept it after ``source`` last changed."""
    if (target_root / "pages" / relative).exists():
        return False
    since = datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc).strftime(HISTORY_STAMP)
    kept = re.compile(rf"{re.escape(relative.stem)}\.(\d{{8}}T\d{{6}}Z)(-\d+)?\.md")
    return any((match := kept.fullmatch(path.name)) is not None and match[1] > since
               for path in (target_root / HISTORY_DIRNAME / relative.parent).glob("*.md"))


def promote_wiki_pages(workspace: Path, *, vertical: str, shared_root: Path) -> dict[str, list[str]]:
    """Copy audience-tagged pages of every Wiki under ``workspace`` into ``shared_root``.

    Returns the pages copied into each shared Wiki -- ``global`` or a
    vertical's name -- as paths relative to ``pages/``. Untagged, unreadable
    or malformed pages are skipped; a shared copy that is at least as new as
    the project page is left alone. INDEX.md of the shared Wiki gains one line
    per newly listed page.
    """
    promoted: dict[str, list[str]] = {}
    try:
        wiki_roots = discover_wikis(Path(workspace))
    except OSError:
        log.exception("wiki promotion: could not list the project Wikis")
        return promoted
    for wiki_root in wiki_roots:
        library = Path(os.path.normpath(wiki_root.absolute()))
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
            page_vertical = declared_vertical(text, vertical) if audience == "vertical" else ""
            target_root = shared_target_root(shared_root, audience=audience, vertical=page_vertical)
            if target_root is None:
                continue
            rel_posix = relative.as_posix()
            try:
                if _retired_since(source, target_root, relative):
                    continue
                copied = _copy_if_newer(source, target_root / "pages" / relative)
                # The checks a page links to travel with it, so its links hold
                # in the shared copy.
                for code in linked_files(source, text, library):
                    if code.is_file() and not code.is_symlink() and code.resolve().is_relative_to(library.resolve()):
                        _copy_if_newer(code, target_root / code.relative_to(library))
                if not copied:
                    continue
                _append_index_line(
                    target_root,
                    audience=audience,
                    vertical=page_vertical,
                    relative=rel_posix,
                    title=page.title,
                    description=page.description,
                )
            except OSError:
                log.exception("wiki promotion: could not copy %s into %s", source, target_root)
                continue
            promoted.setdefault(page_vertical or "global", []).append(rel_posix)
    return promoted


__all__ = ["declared_vertical", "linked_files", "promote_wiki_pages", "shared_target_root"]
