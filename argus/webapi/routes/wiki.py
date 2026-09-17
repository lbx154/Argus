"""Read-only view of the knowledge Wikis for human readers.

Agents maintain ``<workspace>/.autors/<project>/wiki`` themselves (INDEX.md
plus semantic pages under ``pages/``). The host keeps two more tiers of the
same shape under ``<ARGUS_SKILL_HOME>/wiki``: ``_global/`` for every project
and ``_shared_verticals/<vertical>/`` for the projects of one vertical, filled
by copying reviewed project pages. These routes only locate those Wikis, list
their pages newest-first and serve one page at a time, so the knowledge browser
can show what a project, its vertical and the host have learned.

Each page row also carries what its front matter says about it (``kind``,
``source``, ``created``) and how many times the host has handed it to a role,
read from the knowledge journal (:mod:`argus.wiki.journal`). A vertical library
adds its ``principles.md`` when one has been compiled, and ``/api/knowledge/feed``
serves the journal itself newest-first. Nothing here writes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import Depends, HTTPException, Query

from ...core import paths as core_paths
from ...wiki.auto_hooks import discover_wikis
from ...wiki.journal import read_knowledge_events, reuse_counts
from ...wiki.schema import parse_page
from .context import ServerContext

INDEX_FILENAME = "INDEX.md"
PRINCIPLES_FILENAME = "principles.md"
INDEX_LIMIT = 32 * 1024
PAGE_LIMIT = 128 * 1024
PAGE_COUNT_LIMIT = 200
FEED_LIMIT = 500
SCOPES = ("global", "vertical", "project")
# What a knowledge page is, as named by its front matter; anything else is a page.
PAGE_KINDS = ("fact", "lesson", "survey", "principles", "page")
_HEADING = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

ReuseCounts = dict[tuple[str, str, str], int]


def _read_text(path: Path, limit: int) -> tuple[str, bool]:
    """UTF-8 text bounded to ``limit`` bytes plus whether it was cut."""
    data = path.read_bytes()
    truncated = len(data) > limit
    return data[:limit].decode("utf-8", errors="replace"), truncated


def _strip_front_matter(text: str) -> str:
    """The Markdown body without a leading front matter block."""
    if not text.startswith("---\n"):
        return text
    _front, separator, content = text[4:].partition("\n---\n")
    return content.lstrip("\n") if separator else text


def _front_matter(text: str) -> dict[str, Any]:
    """The front matter mapping, or an empty dict when there is none or it is not a mapping."""
    if not text.startswith("---\n"):
        return {}
    front, separator, _content = text[4:].partition("\n---\n")
    if not separator:
        return {}
    try:
        loaded = yaml.safe_load(front)
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _scalar(value: Any) -> str:
    """A front matter value as text; dates keep their ISO form."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value).strip()


def _page_meta(text: str, fallback: str) -> dict[str, str]:
    """title/description/kind/source/created from the front matter, with fallbacks.

    A page without valid front matter takes its title from the first H1 (or the
    file stem), an empty description, and is a plain ``page``.
    """
    meta = {"title": fallback, "description": "", "kind": "page", "source": "", "created": ""}
    try:
        page = parse_page(text)
    except (ValueError, yaml.YAMLError):
        match = _HEADING.search(text)
        if match:
            meta["title"] = match.group(1).strip()
        return meta
    meta["title"], meta["description"] = page.title, page.description
    front = _front_matter(text)
    kind = _scalar(front.get("kind")).lower()
    meta["kind"] = kind if kind in PAGE_KINDS else "page"
    meta["source"] = _scalar(front.get("source"))
    meta["created"] = _scalar(front.get("created"))
    return meta


def _reuse_count(counts: ReuseCounts, scope: str, vertical: str, path: str) -> int:
    """Recalls of one page. A recall that named no vertical still counts for the page."""
    total = counts.get((scope, vertical, path), 0)
    if vertical:
        total += counts.get((scope, "", path), 0)
    return total


def _page_rows(wiki_root: Path) -> list[dict[str, Any]]:
    pages_root = (wiki_root / "pages").resolve()
    rows: list[dict[str, Any]] = []
    for path in pages_root.rglob("*.md"):
        relative = path.relative_to(pages_root)
        if any(part.startswith(".") for part in relative.parts) or not path.is_file():
            continue
        try:
            # A symlink pointing outside pages/ is not a page and is never read.
            if not path.resolve().is_relative_to(pages_root):
                continue
            text, _ = _read_text(path, PAGE_LIMIT)
            updated_at = path.stat().st_mtime
        except OSError:
            continue
        rows.append(
            {
                "path": (Path("pages") / relative).as_posix(),
                **_page_meta(text, path.stem),
                "updated_at": updated_at,
            }
        )
    rows.sort(key=lambda row: (-row["updated_at"], row["path"]))
    return rows[:PAGE_COUNT_LIMIT]


def _with_reuse_counts(
    rows: list[dict[str, Any]], counts: ReuseCounts, *, scope: str, vertical: str
) -> list[dict[str, Any]]:
    return [
        {**row, "reuse_count": _reuse_count(counts, scope, vertical, row["path"])}
        for row in rows
    ]


def _index_markdown(root: Path) -> str:
    try:
        text, _ = _read_text(root / INDEX_FILENAME, INDEX_LIMIT)
    except OSError:
        return ""
    return text


def _principles_markdown(root: Path) -> str | None:
    """The library's compiled principles without their front matter; None until there are any."""
    path = root / PRINCIPLES_FILENAME
    try:
        if not path.is_file():
            return None
        text, _ = _read_text(path, PAGE_LIMIT)
    except OSError:
        return None
    return _strip_front_matter(text)


def _read_page(root: Path, path: str) -> dict[str, Any]:
    """One page under ``root/pages``; 409 for a path that leaves it, 404 when absent."""
    root = root.resolve()
    pages_root = (root / "pages").resolve()
    candidate = Path(path)
    if candidate.is_absolute() or any(part in {"..", ""} for part in candidate.parts):
        raise HTTPException(status_code=409, detail="Wiki page path must stay inside the wiki")
    try:
        target = (root / candidate).resolve()
    except OSError as exc:
        raise HTTPException(status_code=409, detail=f"Cannot resolve wiki page: {exc}") from exc
    if not target.is_relative_to(pages_root) or target.suffix.casefold() != ".md":
        raise HTTPException(status_code=409, detail="Wiki page path must stay under pages/")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Wiki page not found")
    try:
        markdown, truncated = _read_text(target, PAGE_LIMIT)
        updated_at = target.stat().st_mtime
    except OSError as exc:
        raise HTTPException(status_code=409, detail="Cannot read wiki page") from exc
    return {
        "path": (Path("pages") / target.relative_to(pages_root)).as_posix(),
        **_page_meta(markdown, target.stem),
        "content": _strip_front_matter(markdown),
        "markdown": markdown,
        "truncated": truncated,
        "updated_at": updated_at,
    }


def _shared_wiki_exists(root: Path) -> bool:
    try:
        return (root / "pages").is_dir()
    except OSError:
        return False


def _shared_vertical_roots(global_root: Path) -> list[tuple[str, Path]]:
    """(vertical, wiki root) for every shared vertical Wiki that has pages."""
    parent = core_paths.shared_wiki_root(global_root) / "_shared_verticals"
    try:
        children = sorted(parent.iterdir()) if parent.is_dir() else []
    except OSError:
        return []
    return [
        (child.name, child)
        for child in children
        if child.is_dir() and not child.name.startswith((".", "_")) and _shared_wiki_exists(child)
    ]


def _library(
    scope: str, vertical: str, root: Path, *, root_label: str, counts: ReuseCounts
) -> dict[str, Any]:
    return {
        "scope": scope,
        "vertical": vertical,
        "root": root_label,
        "index_markdown": _index_markdown(root),
        "pages": _with_reuse_counts(_page_rows(root), counts, scope=scope, vertical=vertical),
        # Principles are compiled per shared library; a project keeps none of its own.
        "principles": _principles_markdown(root) if scope != "project" else None,
    }


def register_wiki_routes(app, ctx: ServerContext) -> None:
    def wiki_root(sid: str) -> tuple[Path, Path] | None:
        """(workspace, wiki root) for the first discovered Wiki, or None."""
        from ..artifacts import project_workspace

        workspace = project_workspace(sid, global_root=ctx.project_root_or_404(sid))
        if workspace is None:
            return None
        workspace = workspace.resolve()
        try:
            roots = discover_wikis(workspace)
        except OSError:
            return None
        return (workspace, roots[0]) if roots else None

    def active_vertical(sid: str) -> str:
        from ...skills.vertical_select import resolve_project_vertical
        from ..artifacts import project_workspace

        workspace = project_workspace(sid, global_root=ctx.project_root_or_404(sid))
        if workspace is None:
            return ""
        try:
            return resolve_project_vertical(workspace, life_dir=ctx.resolve_or_404(sid))
        except Exception:  # noqa: BLE001 - an unreadable project state hides nothing else
            return ""

    def relative_root(workspace: Path, root: Path) -> str:
        try:
            return root.resolve().relative_to(workspace).as_posix()
        except ValueError:
            return root.as_posix()

    def shared_root_for(scope: str, vertical: str, global_root: Path) -> Path | None:
        if scope == "global":
            root = core_paths.global_wiki_root(global_root)
        elif scope == "vertical":
            try:
                root = core_paths.shared_vertical_wiki_root(vertical, global_root)
            except ValueError:
                return None
        else:
            return None
        return root if _shared_wiki_exists(root) else None

    @app.get("/api/wiki", dependencies=[Depends(ctx.require_auth)])
    def _knowledge(sid: str | None = Query(None, max_length=128)) -> dict[str, Any]:
        global_root = ctx.project_root_or_404(sid) if sid else ctx.roots[0]
        counts = reuse_counts(global_root)
        libraries: list[dict[str, Any]] = []
        active = ""
        if sid:
            active = active_vertical(sid)
            located = wiki_root(sid)
            if located is not None:
                workspace, root = located
                libraries.append(
                    _library(
                        "project", active, root,
                        root_label=relative_root(workspace, root), counts=counts,
                    )
                )
        shared_verticals = _shared_vertical_roots(global_root)
        for vertical, root in shared_verticals:
            libraries.append(
                _library("vertical", vertical, root, root_label=str(root), counts=counts)
            )
        global_wiki = core_paths.global_wiki_root(global_root)
        if _shared_wiki_exists(global_wiki):
            libraries.append(
                _library("global", "", global_wiki, root_label=str(global_wiki), counts=counts)
            )
        items = [
            {**page, "scope": library["scope"], "vertical": library["vertical"], "root": library["root"]}
            for library in libraries
            for page in library["pages"]
        ]
        items.sort(key=lambda row: (-row["updated_at"], row["path"]))
        verticals = sorted({name for name, _root in shared_verticals} | ({active} if active else set()))
        return {
            "scopes": list(SCOPES),
            "libraries": libraries,
            "items": items,
            "verticals": verticals,
            "active_vertical": active,
            "errors": [],
        }

    @app.get("/api/wiki/page", dependencies=[Depends(ctx.require_auth)])
    def _knowledge_page(
        scope: str = Query(..., max_length=32),
        path: str = Query(..., max_length=1024),
        vertical: str = Query("", max_length=128),
        sid: str | None = Query(None, max_length=128),
    ) -> dict[str, Any]:
        if scope not in SCOPES:
            raise HTTPException(status_code=404, detail=f"Unknown knowledge scope: {scope}")
        if scope == "project":
            if not sid:
                raise HTTPException(status_code=404, detail="A project page needs its project id")
            located = wiki_root(sid)
            if located is None:
                raise HTTPException(status_code=404, detail="Project has no wiki")
            _workspace, root = located
            vertical = active_vertical(sid)
        else:
            if scope == "vertical" and not vertical:
                raise HTTPException(status_code=404, detail="A vertical page needs its vertical")
            global_root = ctx.project_root_or_404(sid) if sid else ctx.roots[0]
            shared = shared_root_for(scope, vertical, global_root)
            if shared is None:
                raise HTTPException(status_code=404, detail="Shared wiki not found")
            root = shared
            if scope == "global":
                vertical = ""
        page = _read_page(root, path)
        return {"scope": scope, "vertical": vertical, **page}

    @app.get("/api/knowledge/feed", dependencies=[Depends(ctx.require_auth)])
    def _knowledge_feed(
        limit: int = Query(50, ge=1, le=FEED_LIMIT),
        kind: str = Query("", max_length=64),
        sid: str | None = Query(None, max_length=128),
    ) -> dict[str, Any]:
        """The knowledge journal newest-first: what was learned, recalled and shared."""
        global_root = ctx.project_root_or_404(sid) if sid else ctx.roots[0]
        kinds = [part.strip() for part in kind.split(",") if part.strip()] or None
        return {"events": read_knowledge_events(global_root, limit=limit, kinds=kinds)}

    @app.get("/api/projects/{sid}/wiki", dependencies=[Depends(ctx.require_auth)])
    def _wiki(sid: str) -> dict[str, Any]:
        located = wiki_root(sid)
        if located is None:
            return {"exists": False}
        workspace, root = located
        counts = reuse_counts(ctx.project_root_or_404(sid))
        return {
            "exists": True,
            "root": relative_root(workspace, root),
            "index_markdown": _index_markdown(root),
            "pages": _with_reuse_counts(
                _page_rows(root), counts, scope="project", vertical=active_vertical(sid)
            ),
        }

    @app.get("/api/projects/{sid}/wiki/page", dependencies=[Depends(ctx.require_auth)])
    def _page(sid: str, path: str = Query(..., max_length=1024)) -> dict[str, Any]:
        located = wiki_root(sid)
        if located is None:
            raise HTTPException(status_code=404, detail="Project has no wiki")
        _workspace, root = located
        return _read_page(root, path)
