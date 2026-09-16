"""Read-only view of a project's shared Wiki for human readers.

Agents maintain ``<workspace>/.autors/<project>/wiki`` themselves (INDEX.md
plus semantic pages under ``pages/``). This route only locates the first such
Wiki, lists its pages newest-first and serves one page at a time, so the Atlas
sidebar can show what the project has learned while it runs. Nothing here
writes, gates a stage or admits a task.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Query

from ...wiki.auto_hooks import discover_wikis
from ...wiki.schema import parse_page
from .context import ServerContext

INDEX_FILENAME = "INDEX.md"
INDEX_LIMIT = 32 * 1024
PAGE_LIMIT = 128 * 1024
PAGE_COUNT_LIMIT = 200
_HEADING = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def _read_text(path: Path, limit: int) -> tuple[str, bool]:
    """UTF-8 text bounded to ``limit`` bytes plus whether it was cut."""
    data = path.read_bytes()
    truncated = len(data) > limit
    return data[:limit].decode("utf-8", errors="replace"), truncated


def _title_and_description(text: str, fallback: str) -> tuple[str, str]:
    """Front-matter title/description; the first H1 (or the file stem) otherwise."""
    try:
        page = parse_page(text)
        return page.title, page.description
    except ValueError:
        pass
    match = _HEADING.search(text)
    return (match.group(1).strip() if match else fallback), ""


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
        title, description = _title_and_description(text, path.stem)
        rows.append(
            {
                "path": (Path("pages") / relative).as_posix(),
                "title": title,
                "description": description,
                "updated_at": updated_at,
            }
        )
    rows.sort(key=lambda row: (-row["updated_at"], row["path"]))
    return rows[:PAGE_COUNT_LIMIT]


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

    @app.get("/api/projects/{sid}/wiki", dependencies=[Depends(ctx.require_auth)])
    def _wiki(sid: str) -> dict[str, Any]:
        located = wiki_root(sid)
        if located is None:
            return {"exists": False}
        workspace, root = located
        try:
            index_markdown, _ = _read_text(root / INDEX_FILENAME, INDEX_LIMIT)
        except OSError:
            index_markdown = ""
        try:
            relative_root = root.resolve().relative_to(workspace).as_posix()
        except ValueError:
            relative_root = root.as_posix()
        return {
            "exists": True,
            "root": relative_root,
            "index_markdown": index_markdown,
            "pages": _page_rows(root),
        }

    @app.get("/api/projects/{sid}/wiki/page", dependencies=[Depends(ctx.require_auth)])
    def _page(sid: str, path: str = Query(..., max_length=1024)) -> dict[str, Any]:
        located = wiki_root(sid)
        if located is None:
            raise HTTPException(status_code=404, detail="Project has no wiki")
        _workspace, root = located
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
        title, _ = _title_and_description(markdown, target.stem)
        return {
            "path": (Path("pages") / target.relative_to(pages_root)).as_posix(),
            "title": title,
            "markdown": markdown,
            "truncated": truncated,
            "updated_at": updated_at,
        }
