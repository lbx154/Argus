"""Trajectory index — SQLite + FTS5 over past agent activity.

Inspired by the Obelisk skill (tommy0103/obelisk): the agent itself
should be able to query its own past trajectories instead of relying
solely on LLM-distilled skill cards. Three sources get indexed:

* ``~/.codex/sessions/**/*.jsonl`` — raw codex rollouts (engineer
  trajectory: tool calls, tool results, agent messages).
* ``~/.argus-skill/projects/<life>/inbox.jsonl`` — operator
  directives + subagent reports.
* ``~/.argus-skill/projects/<life>/decisions.jsonl`` — planner /
  engineer / reviewer verdicts.

The index is incremental: on each call we compare ``(mtime, size)`` per
source file against the ``files`` table; only changed files get
re-scanned. Pure stdlib (``sqlite3`` ships with FTS5 enabled in CPython
3.11+).

Coupling with the existing memory system:

* skill cards (curated, distilled) and wiki pages (curated, project)
  remain the source of truth for *promoted* knowledge;
* this index is the *raw evidence substrate* under them. The CLI
  ``argus-skill query`` returns trajectory hits + matching skill slugs
  + matching wiki pages in one shot, so engineer in a tight loop can
  pull all three layers at once.

This module deliberately does NOT mutate skill or wiki frontmatter;
that comes in a follow-up (evidence_uuids backfill + mechanical_promote
rewrite) once the substrate is in place and stable.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

_SCHEMA_VERSION = 1


def default_db_path() -> Path:
    """Return the canonical index db path under ~/.argus-skill/."""
    base = Path(os.environ.get("ARGUS_SKILL_HOME") or (Path.home() / ".argus-skill"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "trajectory_index.sqlite"


def default_codex_root() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "sessions"


def default_argus_projects_root() -> Path:
    base = Path(os.environ.get("ARGUS_SKILL_HOME") or (Path.home() / ".argus-skill"))
    return base / "projects"


# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT
        );
        CREATE TABLE IF NOT EXISTS files (
          path TEXT PRIMARY KEY,
          mtime REAL NOT NULL,
          size INTEGER NOT NULL,
          source TEXT NOT NULL,
          last_indexed REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rows (
          id INTEGER PRIMARY KEY,
          source TEXT NOT NULL,
          source_path TEXT NOT NULL,
          session_id TEXT,
          ts TEXT,
          kind TEXT,
          uuid TEXT,
          text TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS rows_source_path ON rows(source_path);
        CREATE INDEX IF NOT EXISTS rows_session ON rows(session_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS rows_fts USING fts5(
          text,
          content='rows',
          content_rowid='id',
          tokenize='unicode61'
        );
        CREATE TRIGGER IF NOT EXISTS rows_ai AFTER INSERT ON rows BEGIN
          INSERT INTO rows_fts(rowid, text) VALUES (new.id, new.text);
        END;
        CREATE TRIGGER IF NOT EXISTS rows_ad AFTER DELETE ON rows BEGIN
          INSERT INTO rows_fts(rows_fts, rowid, text) VALUES('delete', old.id, old.text);
        END;
        """
    )
    cur.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(_SCHEMA_VERSION),),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Source extractors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    source: str
    source_path: str
    session_id: str | None
    ts: str | None
    kind: str | None
    uuid: str | None
    text: str


def _safe_str(v: object, limit: int = 16384) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        try:
            s = json.dumps(v, ensure_ascii=False)
        except Exception:
            s = str(v)
    else:
        s = str(v)
    return s[:limit]


def _iter_jsonl(path: Path) -> Iterator[dict]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _extract_codex(path: Path) -> Iterator[Row]:
    """Codex rollouts are JSONL with `{timestamp, type, payload}` shape."""
    session_id = path.stem
    for obj in _iter_jsonl(path):
        # codex 0.x shape: top-level {timestamp, type, payload: {...}}
        raw_payload = obj.get("payload")
        payload = raw_payload if isinstance(raw_payload, dict) else obj
        kind = payload.get("type") or obj.get("type") or obj.get("role") or "event"
        ts = obj.get("timestamp") or obj.get("ts") or payload.get("timestamp")
        uuid = payload.get("id") or obj.get("id") or obj.get("uuid")
        text_parts: list[str] = []
        # Pull anything that smells like agent-visible text.
        # Codex tool calls live under payload.{call_id, name, arguments};
        # function output under payload.{output, content}; messages under
        # payload.content (which is a list of {type, text} blocks).
        content = payload.get("content")
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict):
                    t = c.get("text") or c.get("output") or c.get("input_text")
                    if t:
                        text_parts.append(_safe_str(t))
        for key in ("text", "message", "output", "stdout", "command",
                    "input", "name", "arguments", "summary", "reason"):
            v = payload.get(key) if isinstance(payload, dict) else None
            if v:
                text_parts.append(_safe_str(v))
        if not text_parts:
            text_parts.append(_safe_str(payload))
        text = " | ".join(text_parts).strip()
        if not text:
            continue
        yield Row(
            source="codex",
            source_path=str(path),
            session_id=session_id,
            ts=str(ts) if ts is not None else None,
            kind=str(kind),
            uuid=str(uuid) if uuid is not None else None,
            text=text,
        )


def _extract_argus_inbox(path: Path) -> Iterator[Row]:
    life_dir = path.parent.name
    for obj in _iter_jsonl(path):
        ts = obj.get("ts")
        kind = obj.get("kind") or "inbox"
        text = _safe_str(obj.get("text") or obj)
        if not text:
            continue
        yield Row(
            source="argus_inbox",
            source_path=str(path),
            session_id=life_dir,
            ts=str(ts) if ts else None,
            kind=str(kind),
            uuid=None,
            text=text,
        )


def _extract_argus_decisions(path: Path) -> Iterator[Row]:
    life_dir = path.parent.name
    for obj in _iter_jsonl(path):
        ts = obj.get("ts") or obj.get("at")
        kind = obj.get("role") or obj.get("kind") or "decision"
        text_parts: list[str] = []
        for key in ("verdict", "reason", "summary", "message", "text"):
            v = obj.get(key)
            if v:
                text_parts.append(f"{key}={_safe_str(v)}")
        if not text_parts:
            text_parts.append(_safe_str(obj))
        text = " | ".join(text_parts).strip()
        if not text:
            continue
        yield Row(
            source="argus_decisions",
            source_path=str(path),
            session_id=life_dir,
            ts=str(ts) if ts else None,
            kind=str(kind),
            uuid=None,
            text=text,
        )


_SOURCE_EXTRACTORS: dict[str, Callable[[Path], Iterator[Row]]] = {
    "codex": _extract_codex,
    "argus_inbox": _extract_argus_inbox,
    "argus_decisions": _extract_argus_decisions,
}


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


def _discover_files(
    codex_root: Path | None,
    argus_projects: Path | None,
) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    if codex_root and codex_root.exists():
        for p in codex_root.rglob("*.jsonl"):
            out.append((p, "codex"))
    if argus_projects and argus_projects.exists():
        for life in argus_projects.iterdir():
            if not life.is_dir():
                continue
            for name, src in (("inbox.jsonl", "argus_inbox"), ("decisions.jsonl", "argus_decisions")):
                p = life / name
                if p.exists():
                    out.append((p, src))
    return out


def _file_changed(conn: sqlite3.Connection, path: Path) -> tuple[bool, float, int]:
    try:
        st = path.stat()
    except OSError:
        return (False, 0.0, 0)
    cur = conn.execute(
        "SELECT mtime, size FROM files WHERE path=?", (str(path),)
    ).fetchone()
    if cur is None:
        return (True, st.st_mtime, st.st_size)
    return (cur[0] != st.st_mtime or cur[1] != st.st_size, st.st_mtime, st.st_size)


def index_all(
    db_path: Path | None = None,
    codex_root: Path | None = None,
    argus_projects: Path | None = None,
    verbose: bool = False,
) -> dict:
    """Incrementally index all known sources.

    Returns a small report dict with counts of files/rows touched.
    """
    db_path = db_path or default_db_path()
    codex_root = codex_root if codex_root is not None else default_codex_root()
    argus_projects = argus_projects if argus_projects is not None else default_argus_projects_root()

    conn = _connect(db_path)
    try:
        _init_schema(conn)
        files = _discover_files(codex_root, argus_projects)
        files_scanned = 0
        rows_inserted = 0
        for path, source in files:
            extractor = _SOURCE_EXTRACTORS[source]
            changed, mtime, size = _file_changed(conn, path)
            if not changed:
                continue
            files_scanned += 1
            with conn:
                conn.execute(
                    "DELETE FROM rows WHERE source_path=?", (str(path),)
                )
                batch: list[tuple] = []
                for row in extractor(path):
                    batch.append(
                        (row.source, row.source_path, row.session_id,
                         row.ts, row.kind, row.uuid, row.text)
                    )
                    if len(batch) >= 500:
                        conn.executemany(
                            "INSERT INTO rows(source, source_path, session_id, ts, kind, uuid, text) "
                            "VALUES (?,?,?,?,?,?,?)",
                            batch,
                        )
                        rows_inserted += len(batch)
                        batch.clear()
                if batch:
                    conn.executemany(
                        "INSERT INTO rows(source, source_path, session_id, ts, kind, uuid, text) "
                        "VALUES (?,?,?,?,?,?,?)",
                        batch,
                    )
                    rows_inserted += len(batch)
                conn.execute(
                    "INSERT OR REPLACE INTO files(path, mtime, size, source, last_indexed) "
                    "VALUES (?,?,?,?,?)",
                    (str(path), mtime, size, source, time.time()),
                )
            if verbose:
                print(f"[index] {source} {path} +{rows_inserted} rows total")
        total_rows = conn.execute("SELECT COUNT(*) FROM rows").fetchone()[0]
        return {
            "db_path": str(db_path),
            "files_total": len(files),
            "files_scanned": files_scanned,
            "rows_inserted": rows_inserted,
            "rows_total": total_rows,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def _fts_escape(query: str) -> str:
    """Wrap each whitespace-delimited token in quotes so FTS5 treats it
    as a literal phrase (no operator parsing). Empty tokens are dropped.
    """
    toks = [t for t in re.findall(r"[\w][\w\-]*", query) if t]
    if not toks:
        return ""
    return " ".join(f"\"{t}\"" for t in toks)


@dataclass(frozen=True)
class TrajectoryHit:
    source: str
    source_path: str
    session_id: str | None
    ts: str | None
    kind: str | None
    text: str
    score: float


def search_trajectories(
    query: str,
    limit: int = 10,
    db_path: Path | None = None,
    sources: Iterable[str] | None = None,
) -> list[TrajectoryHit]:
    db_path = db_path or default_db_path()
    if not db_path.exists():
        return []
    fts = _fts_escape(query)
    if not fts:
        return []
    conn = _connect(db_path)
    try:
        sql = (
            "SELECT r.source, r.source_path, r.session_id, r.ts, r.kind, r.text, "
            "       bm25(rows_fts) AS score "
            "FROM rows_fts JOIN rows r ON r.id = rows_fts.rowid "
            "WHERE rows_fts MATCH ? "
        )
        params: list = [fts]
        if sources:
            placeholders = ",".join("?" for _ in sources)
            sql += f" AND r.source IN ({placeholders}) "
            params.extend(sources)
        sql += "ORDER BY score LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
        return [
            TrajectoryHit(
                source=r[0],
                source_path=r[1],
                session_id=r[2],
                ts=r[3],
                kind=r[4],
                text=r[5],
                score=float(r[6]) if r[6] is not None else 0.0,
            )
            for r in rows
        ]
    finally:
        conn.close()
