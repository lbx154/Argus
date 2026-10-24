"""Authenticated browser drafts, research-result editions, and history search."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from .. import artifacts
from .context import BROWSER_COOKIE, ServerContext


@contextmanager
def browser_store(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory / "browser-state.sqlite3")) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL, revision INTEGER NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS editions (
            kind TEXT NOT NULL, source TEXT NOT NULL, version INTEGER NOT NULL,
            content TEXT NOT NULL, note TEXT NOT NULL, created REAL NOT NULL,
            PRIMARY KEY(kind, source, version))""")
        yield db


def stored_state(db, key: str) -> dict[str, Any]:
    row = db.execute("SELECT value, revision FROM state WHERE key = ?", (key,)).fetchone()
    return {"value": json.loads(row["value"]), "revision": row["revision"]} if row else {"value": {}, "revision": 0}


class DraftAttachment(BaseModel):
    attachment_id: str = Field(min_length=1, max_length=256)
    relative_path: str = Field(min_length=1, max_length=1024)
    original_name: str = Field(min_length=1, max_length=1024)
    stored_name: str = Field(min_length=1, max_length=1024)
    mime: str = Field(min_length=1, max_length=200)
    size_bytes: int = Field(ge=0)


class DraftValue(BaseModel):
    text: str = Field(default="", max_length=32_000)
    attachments: list[str] = Field(default_factory=list, max_length=5)
    uploaded: list[DraftAttachment] = Field(default_factory=list, max_length=5)


class StateIn(BaseModel):
    value: DraftValue
    revision: int = Field(ge=0)


class OrganizationIn(BaseModel):
    sid: str = Field(min_length=1, max_length=160)
    field: Literal["pinned", "archived", "followup"]
    value: bool


class EditionIn(BaseModel):
    source: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=512 * 1024)
    note: str = Field(default="", max_length=32_000)
    base_version: int = Field(ge=0)
    original: str = Field(default="", max_length=512 * 1024)


def register_experience_routes(app, ctx: ServerContext) -> None:
    root = ctx.roots[0]

    @app.post("/api/browser-session", dependencies=[Depends(ctx.require_auth)])
    def sign_in(request: Request, response: Response):
        ctx.grant_cookie(request, response, BROWSER_COOKIE, "/", max_age=30 * 86400)
        response.headers["Cache-Control"] = "no-store"
        return {"authenticated": True}

    @app.delete("/api/browser-session")
    def sign_out(request: Request, response: Response):
        ctx.require_auth(request)
        response.delete_cookie(BROWSER_COOKIE, path="/", httponly=True, samesite="strict")
        return {"signed_out": True}

    @app.get("/api/browser-state/organization", dependencies=[Depends(ctx.require_auth)])
    def organization(response: Response):
        response.headers["Cache-Control"] = "private, no-store"
        with browser_store(root) as db:
            return stored_state(db, "organization")

    @app.post("/api/browser-state/organization", dependencies=[Depends(ctx.require_auth)])
    def organize(body: OrganizationIn):
        ctx.resolve_or_404(body.sid)
        with browser_store(root) as db:
            db.execute("BEGIN IMMEDIATE")
            current = stored_state(db, "organization")
            preferences = current["value"]
            item = preferences.setdefault(body.sid, {"pinned": False, "archived": False, "followup": False})
            item[body.field] = body.value
            db.execute("INSERT OR REPLACE INTO state VALUES (?, ?, ?)",
                       ("organization", json.dumps(preferences), current["revision"] + 1))
            return {"value": preferences, "revision": current["revision"] + 1}

    @app.get("/api/projects/{sid}/browser-draft", dependencies=[Depends(ctx.require_auth)])
    def draft(sid: str, response: Response):
        response.headers["Cache-Control"] = "private, no-store"
        with browser_store(ctx.resolve_or_404(sid)) as db:
            return stored_state(db, "draft")

    @app.post("/api/projects/{sid}/browser-draft", dependencies=[Depends(ctx.require_auth)])
    def save_draft(sid: str, body: StateIn):
        value = body.value.model_dump()
        if len(json.dumps(value)) > 128 * 1024:
            raise HTTPException(413, "Draft metadata is too large")
        with browser_store(ctx.resolve_or_404(sid)) as db:
            db.execute("BEGIN IMMEDIATE")
            current = stored_state(db, "draft")
            if current["revision"] != body.revision:
                raise HTTPException(409, "Another device changed this draft. Choose which draft to keep.")
            db.execute("INSERT OR REPLACE INTO state VALUES (?, ?, ?)",
                       ("draft", json.dumps(value), body.revision + 1))
            return {"value": value, "revision": body.revision + 1}

    def source_document(sid: str, path: str):
        resolved = artifacts.resolved_project_artifact(sid, path, global_root=ctx.project_root_or_404(sid))
        if resolved is None:
            raise HTTPException(404, "File unavailable or not allowlisted")
        info, file = resolved
        if info["kind"] not in {"markdown", "text", "json", "table", "html"} or info["source"] in {"reader_foundation", "progress_snapshot", "attachment"}:
            raise HTTPException(422, "Only project text results can be edited; other files can still be revised in conversation.")
        if file.stat().st_size > 512 * 1024:
            raise HTTPException(413, "This document is too large for the inline editor; download it to edit.")
        try:
            return file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, "This file is not UTF-8 text") from exc

    @app.get("/api/projects/{sid}/editions/{kind}", dependencies=[Depends(ctx.require_auth)])
    def editions(sid: str, kind: Literal["artifact", "conversation"], response: Response,
                 source: str = Query(min_length=1, max_length=512)):
        response.headers["Cache-Control"] = "private, no-store"
        original = source_document(sid, source) if kind == "artifact" else ""
        with browser_store(ctx.resolve_or_404(sid)) as db:
            rows = db.execute("SELECT version, content, note, created FROM editions WHERE kind=? AND source=? ORDER BY version",
                              (kind, source)).fetchall()
        return {"original": original, "versions": [dict(row) for row in rows]}

    @app.post("/api/projects/{sid}/editions/{kind}", dependencies=[Depends(ctx.require_auth)])
    def save_edition(sid: str, kind: Literal["artifact", "conversation"], body: EditionIn):
        original = source_document(sid, body.source) if kind == "artifact" else body.original
        with browser_store(ctx.resolve_or_404(sid)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT MAX(version) FROM editions WHERE kind=? AND source=?", (kind, body.source)).fetchone()
            latest = row[0] or 0
            if latest != body.base_version:
                raise HTTPException(409, "A newer version exists. Reload versions before saving.")
            if latest == 0:
                db.execute("INSERT INTO editions VALUES (?, ?, 0, ?, '', ?)",
                           (kind, body.source, original, time.time()))
            version = latest + 1
            db.execute("INSERT INTO editions VALUES (?, ?, ?, ?, ?, ?)",
                       (kind, body.source, version, body.content, body.note, time.time()))
            return {"version": version, "content": body.content}

    @app.get("/api/search", dependencies=[Depends(ctx.require_auth)])
    async def search(response: Response, q: str = Query(min_length=1, max_length=200),
                     limit: int = Query(50, ge=1, le=100)):
        response.headers["Cache-Control"] = "private, no-store"
        projects = await ctx.machine_projects_async(limit=2000, include_empty=False)
        return await ctx.query_executor.wait(ctx.query_executor.submit(lambda: search_projects(ctx, projects, q, limit)))


def search_projects(ctx: ServerContext, projects: list[dict], query: str, limit: int):
    terms = query.casefold().split()
    if not terms:
        raise HTTPException(422, "Enter at least one search term")
    results: list[dict] = []
    errors: list[str] = []
    scanned = 0
    partial = len(projects) == 2000

    def match(sid, title, text, kind, **extra):
        nonlocal partial
        if not all(term in text.casefold() for term in terms):
            return
        if len(results) >= limit:
            partial = True
            return
        offset = max(0, text.casefold().find(terms[0]) - 65)
        excerpt = text if len(text) <= 32_000 else text[max(0, offset - 1000):offset + 31_000]
        results.append({"sid": sid, "title": title, "kind": kind, "snippet": text[offset:offset + 260],
                        "text": excerpt, "truncated": len(text) > 32_000, **extra})

    for project in projects:
        if len(results) >= limit or scanned >= 32 * 1024 * 1024:
            partial = True
            break
        sid = project["id"]
        title = project.get("label") or project.get("display_name") or project.get("objective") or sid
        match(sid, title, title + "\n" + str(project.get("objective") or ""), "project")
        life = ctx.resolve_or_404(sid)
        transcript = life / "transcript.jsonl"
        if transcript.exists():
            with transcript.open(encoding="utf-8") as stream:
                for number, raw in enumerate(stream, 1):
                    scanned += len(raw.encode("utf-8"))
                    if scanned >= 32 * 1024 * 1024 or len(results) >= limit:
                        partial = True
                        break
                    try:
                        turn = json.loads(raw)
                    except json.JSONDecodeError:
                        if len(errors) < 20:
                            errors.append(f"{sid}: unreadable transcript line {number}")
                        continue
                    if isinstance(turn, dict) and isinstance(turn.get("text"), str):
                        match(sid, title, turn["text"], "conversation", role=turn.get("role", ""),
                              ts=turn.get("ts", 0), line=number, message_id=turn.get("message_id", ""))
        root = ctx.project_root_or_404(sid)
        for artifact in artifacts.list_project_artifacts(sid, global_root=root) or []:
            if scanned >= 32 * 1024 * 1024 or len(results) >= limit:
                partial = True
                break
            if not artifact["exists"] or artifact["kind"] not in {"markdown", "text", "json", "table", "html"}:
                continue
            if artifact["size"] > 512 * 1024:
                partial = True
                continue
            document = artifacts.get_project_artifact(sid, artifact["path"], global_root=root, preview_bytes=512 * 1024)
            if document is not None:
                text = str(document.get("preview") or "")
                scanned += len(text.encode("utf-8"))
                match(sid, title, text, "artifact", path=artifact["path"])
        database = life / "browser-state.sqlite3"
        if database.exists():
            with browser_store(life) as db:
                for row in db.execute("SELECT source, version, content FROM editions WHERE kind='artifact' ORDER BY version DESC"):
                    scanned += len(row["content"].encode("utf-8"))
                    if scanned >= 32 * 1024 * 1024 or len(results) >= limit:
                        partial = True
                        break
                    match(sid, title, row["content"], "edition", path=row["source"], version=row["version"])
    return {"results": results, "partial": partial, "errors": errors}
