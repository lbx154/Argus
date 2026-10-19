"""Rebuildable local recall index, never the authority for an experience.

The default vectors hash lexical tokens. They are NOT semantic embeddings.
An explicit embedding adapter can replace them without changing source records.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import stat
from contextlib import closing, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol, Sequence

from ..core.file_lock import current_file_lock_wait_budget

_TOKEN_RE = re.compile(r"[\w-]{2,}", re.UNICODE)
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_SCHEMA_VERSION = "3"


class EmbeddingUnavailable(ValueError):
    """A bounded external embedding failed; retain canonical lexical recall."""


def _guard_recall_path(path: Path) -> None:
    """Reject existing aliases and special files at a project-state boundary."""
    path = Path(path).absolute()
    if path.parent.resolve() != path.parent or path.is_symlink():
        raise ValueError("recall state must not follow filesystem aliases")
    try:
        mode = path.stat(follow_symlinks=False).st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISREG(mode):
        raise ValueError("recall state must be a regular file")


def _guard_recall_database(path: Path) -> None:
    for candidate in [path, *(path.with_name(path.name + suffix) for suffix in ("-journal", "-wal", "-shm"))]:
        _guard_recall_path(candidate)


def _embedding_batch(embedder: EmbeddingAdapter):
    batch = getattr(embedder, "batch", None)
    return batch() if callable(batch) else nullcontext()


def _index_text(embedder: EmbeddingAdapter, text: str) -> str:
    from ..core.secret_guard import redact_secrets_text

    redact = getattr(embedder, "redact", None)
    return redact(text) if callable(redact) else redact_secrets_text(text)


def tokens(*values: Any) -> set[str]:
    result: set[str] = set()
    for value in values:
        text = str(value or "").casefold()
        result.update(_TOKEN_RE.findall(text))
        for run in _CJK_RE.findall(text):
            result.update(run[index : index + 2] for index in range(len(run) - 1))
    return result


class EmbeddingAdapter(Protocol):
    """Caller-configured, versioned embedding function; no implicit service calls."""

    identifier: str
    dimensions: int

    def embed(self, text: str) -> Sequence[float]: ...


class LexicalEmbedding:
    """No embedding: recall ranks by shared terms alone (see ``lexical_scores``)."""

    identifier = "lexical-v2"
    dimensions = 1

    def embed(self, text: str) -> Sequence[float]:
        return [1.0]


def is_lexical(embedder: EmbeddingAdapter) -> bool:
    """Whether ``embedder`` carries no meaning beyond the shared terms."""
    return embedder.identifier == LexicalEmbedding.identifier


def _normalized(vector: Sequence[float], dimensions: int) -> list[float]:
    if type(dimensions) is not int or not 1 <= dimensions <= 4096 or len(vector) != dimensions:
        raise ValueError("embedding dimensions must match an adapter in [1, 4096]")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in vector):
        raise ValueError("embedding values must be numbers")
    values = [float(value) for value in vector]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("embedding values must be finite")
    scale = max(map(abs, values), default=0.0)
    scaled = [value / scale for value in values] if scale else values
    norm = math.sqrt(sum(value * value for value in scaled))
    return [value / norm for value in scaled] if norm else scaled


@dataclass(frozen=True)
class RecallDocument:
    id: str
    revision: int
    # Identifies this version of the indexed text; compared as is, never hashed.
    version: str
    direct: str
    transfer: str
    ignored_terms: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RecallScore:
    direct: int = 0
    transfer: int = 0
    vector: float = 0.0


def lexical_scores(documents: Sequence[RecallDocument], query: str) -> dict[str, RecallScore]:
    terms = tokens(query)
    return {
        item.id: RecallScore(
            direct=len((terms & tokens(item.direct)) - item.ignored_terms),
            transfer=len((terms & tokens(item.transfer)) - item.ignored_terms),
        )
        for item in documents
    }


class FailureExperienceIndex:
    def __init__(self, path: Path, *, embedder: EmbeddingAdapter | None = None) -> None:
        self.path = Path(path)
        self.embedder = embedder or LexicalEmbedding()
        if (not self.embedder.identifier or len(self.embedder.identifier) > 2048
                or type(self.embedder.dimensions) is not int or not 1 <= self.embedder.dimensions <= 4096):
            raise ValueError(
                "embedding adapter requires a versioned identity and bounded dimensions"
            )

    def _open(self) -> sqlite3.Connection:
        budget = current_file_lock_wait_budget()
        if budget is not None and budget[1] is not None and budget[1]():
            raise EmbeddingUnavailable("recall index read cancelled")
        _guard_recall_database(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _guard_recall_database(self.path)
        # SQLite's built-in busy wait cannot poll the role's Stop callback.
        # A disposable cache can fall back to current lexical facts promptly;
        # callers outside an explicitly cancellable read keep their timeout.
        db = sqlite3.connect(self.path, timeout=0.05 if budget is not None else 5)
        try:
            db.execute("PRAGMA auto_vacuum=FULL")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA secure_delete=ON")
            db.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS documents ("
                "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, version TEXT NOT NULL, "
                "direct_terms TEXT NOT NULL, transfer_terms TEXT NOT NULL, vector TEXT NOT NULL)"
            )
            db.commit()
            return db
        except BaseException:
            db.close()
            raise

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            with closing(self._open()) as db:
                yield db
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            if current_file_lock_wait_budget() is not None and code in {
                sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED,
            }:
                # Contention is not corruption: do not unlink/rebuild a cache
                # still used by another connection, or enter a second wait.
                raise EmbeddingUnavailable("recall index busy; use current lexical recall") from exc
            raise

    def sync(self, documents: Sequence[RecallDocument]) -> None:
        """Replace stale revisions and delete removed identities in one transaction."""
        with self._connection() as db, db:
            db.execute("BEGIN")
            metadata = dict(db.execute("SELECT key, value FROM metadata"))
            existing = {
                identity: (revision, version)
                for identity, revision, version in db.execute(
                    "SELECT id, revision, version FROM documents"
                )
            }
        encoder = f"{self.embedder.identifier}:{self.embedder.dimensions}"
        current = {item.id: (item.revision, item.version) for item in documents}
        encoder_current = metadata.get("encoder") == encoder and metadata.get("schema_version") == _SCHEMA_VERSION
        if encoder_current and existing == current:
            return
        # No index transaction spans an external request. Successful vectors
        # can be reused by a later bounded batch if this batch is interrupted.
        prepared = []
        with _embedding_batch(self.embedder):
            for item in documents:
                if existing.get(item.id) == (item.revision, item.version) and encoder_current:
                    continue
                direct = _index_text(self.embedder, item.direct)
                transfer = _index_text(self.embedder, item.transfer)
                vector = _normalized(
                    self.embedder.embed(direct + "\n" + transfer),
                    self.embedder.dimensions,
                )
                prepared.append((item.id, item.revision, item.version,
                                 json.dumps(sorted(tokens(direct) - item.ignored_terms)),
                                 json.dumps(sorted(tokens(transfer) - item.ignored_terms)), json.dumps(vector)))
        with self._connection() as db, db:
            db.execute("BEGIN IMMEDIATE")
            latest = dict(db.execute("SELECT key, value FROM metadata"))
            latest_rows = {identity: (revision, version) for identity, revision, version in db.execute(
                "SELECT id, revision, version FROM documents"
            )}
            if latest != metadata or latest_rows != existing:
                raise EmbeddingUnavailable("recall index changed during vector preparation")
            if not encoder_current:
                db.execute("DELETE FROM documents")
            db.executemany("DELETE FROM documents WHERE id=?", [(identity,) for identity in existing.keys() - current.keys()])
            db.executemany(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, version=excluded.version, "
                "direct_terms=excluded.direct_terms, transfer_terms=excluded.transfer_terms, vector=excluded.vector",
                prepared,
            )
            db.executemany(
                "INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                [
                    ("encoder", encoder),
                    ("schema_version", _SCHEMA_VERSION),
                ],
            )

    def scores(self, query: str, *, documents: Sequence[RecallDocument]) -> dict[str, RecallScore]:
        query = _index_text(self.embedder, query)
        query_terms = tokens(query)
        with _embedding_batch(self.embedder):
            query_vector = _normalized(self.embedder.embed(query), self.embedder.dimensions)
        with self._connection() as db, db:
            db.execute("BEGIN")
            metadata = dict(db.execute("SELECT key, value FROM metadata"))
            rows = list(db.execute(
                "SELECT id, revision, version, direct_terms, transfer_terms, vector FROM documents"
            ))
            if ({identity: (revision, version) for identity, revision, version, *_ in rows}
                    != {item.id: (item.revision, item.version) for item in documents}
                    or metadata.get("encoder") != f"{self.embedder.identifier}:{self.embedder.dimensions}"
                    or metadata.get("schema_version") != _SCHEMA_VERSION):
                raise ValueError("recall index does not match the current source")
        scores: dict[str, RecallScore] = {}
        for identity, _revision, _version, direct, transfer, vector in rows:
            values = _normalized(json.loads(vector), self.embedder.dimensions)
            scores[identity] = RecallScore(
                len(query_terms & set(json.loads(direct))),
                len(query_terms & set(json.loads(transfer))),
                sum(a * b for a, b in zip(query_vector, values, strict=True)),
            )
        return scores

    def rebuild(self, documents: Sequence[RecallDocument]) -> None:
        """A corrupt/missing cache has no bearing on canonical source state."""
        _guard_recall_database(self.path)
        self.path.unlink(missing_ok=True)
        for suffix in ("-journal", "-wal", "-shm"):
            self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)
        self.sync(documents)

    def compact(self) -> None:
        if self.path.exists():
            with self._connection() as db:
                db.execute("VACUUM")
