"""Rebuildable local recall index, never the authority for an experience.

The default vectors hash lexical tokens. They are NOT semantic embeddings.
An explicit embedding adapter can replace them without changing source records.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

_TOKEN_RE = re.compile(r"[\w-]{2,}", re.UNICODE)
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_SCHEMA_VERSION = "1"


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


class LexicalHashEmbedding:
    identifier = "lexical-hash-v1-256"
    dimensions = 256

    def embed(self, text: str) -> Sequence[float]:
        vector = [0.0] * self.dimensions
        for token in tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            vector[int.from_bytes(digest[:4], "big") % self.dimensions] += (
                1.0 if digest[4] & 1 else -1.0
            )
        return vector


def _normalized(vector: Sequence[float], dimensions: int) -> list[float]:
    if not 1 <= dimensions <= 4096 or len(vector) != dimensions:
        raise ValueError("embedding dimensions must match an adapter in [1, 4096]")
    values = [float(value) for value in vector]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("embedding values must be finite")
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values] if norm else values


@dataclass(frozen=True)
class RecallDocument:
    id: str
    revision: int
    digest: str
    direct: str
    transfer: str


@dataclass(frozen=True)
class RecallScore:
    direct: int = 0
    transfer: int = 0
    vector: float = 0.0


def lexical_scores(documents: Sequence[RecallDocument], query: str) -> dict[str, RecallScore]:
    terms = tokens(query)
    return {
        item.id: RecallScore(
            direct=len(terms & tokens(item.direct)),
            transfer=len(terms & tokens(item.transfer)),
        )
        for item in documents
    }


class FailureExperienceIndex:
    def __init__(self, path: Path, *, embedder: EmbeddingAdapter | None = None) -> None:
        self.path = Path(path)
        self.embedder = embedder or LexicalHashEmbedding()
        if not self.embedder.identifier or not 1 <= self.embedder.dimensions <= 4096:
            raise ValueError(
                "embedding adapter requires a versioned identity and bounded dimensions"
            )

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA auto_vacuum=FULL")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS documents ("
                "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, digest TEXT NOT NULL, "
                "direct_terms TEXT NOT NULL, transfer_terms TEXT NOT NULL, vector TEXT NOT NULL)"
            )
            db.commit()
            return db
        except BaseException:
            db.close()
            raise

    def sync(self, documents: Sequence[RecallDocument], source_digest: str) -> None:
        """Replace stale revisions and delete removed identities in one transaction."""
        with closing(self._open()) as db, db:
            metadata = dict(db.execute("SELECT key, value FROM metadata"))
            encoder = f"{self.embedder.identifier}:{self.embedder.dimensions}"
            existing = {
                identity: (revision, digest)
                for identity, revision, digest in db.execute(
                    "SELECT id, revision, digest FROM documents"
                )
            }
            if (
                metadata.get("source_digest") == source_digest
                and metadata.get("encoder") == encoder
                and metadata.get("schema_version") == _SCHEMA_VERSION
                and existing == {item.id: (item.revision, item.digest) for item in documents}
            ):
                return
            if metadata.get("encoder") != encoder:
                db.execute("DELETE FROM documents")
            current = {item.id for item in documents}
            db.executemany(
                "DELETE FROM documents WHERE id=?",
                [(identity,) for identity in existing.keys() - current],
            )
            for item in documents:
                if (
                    existing.get(item.id) == (item.revision, item.digest)
                    and metadata.get("encoder") == encoder
                ):
                    continue
                vector = _normalized(
                    self.embedder.embed(item.direct + "\n" + item.transfer),
                    self.embedder.dimensions,
                )
                db.execute(
                    "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, digest=excluded.digest, "
                    "direct_terms=excluded.direct_terms, transfer_terms=excluded.transfer_terms, vector=excluded.vector",
                    (
                        item.id,
                        item.revision,
                        item.digest,
                        json.dumps(sorted(tokens(item.direct))),
                        json.dumps(sorted(tokens(item.transfer))),
                        json.dumps(vector),
                    ),
                )
            db.executemany(
                "INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                [
                    ("source_digest", source_digest),
                    ("encoder", encoder),
                    ("schema_version", _SCHEMA_VERSION),
                ],
            )

    def scores(self, query: str, *, source_digest: str) -> dict[str, RecallScore]:
        with closing(self._open()) as db:
            metadata = dict(db.execute("SELECT key, value FROM metadata"))
            if metadata.get("source_digest") != source_digest:
                raise ValueError("recall index does not match the current source")
            query_terms = tokens(query)
            query_vector = _normalized(self.embedder.embed(query), self.embedder.dimensions)
            scores: dict[str, RecallScore] = {}
            for identity, direct, transfer, vector in db.execute(
                "SELECT id, direct_terms, transfer_terms, vector FROM documents"
            ):
                values = _normalized(json.loads(vector), self.embedder.dimensions)
                scores[identity] = RecallScore(
                    len(query_terms & set(json.loads(direct))),
                    len(query_terms & set(json.loads(transfer))),
                    sum(a * b for a, b in zip(query_vector, values, strict=True)),
                )
            return scores

    def rebuild(self, documents: Sequence[RecallDocument], source_digest: str) -> None:
        """A corrupt/missing cache has no bearing on canonical source state."""
        self.path.unlink(missing_ok=True)
        for suffix in ("-journal", "-wal", "-shm"):
            self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)
        self.sync(documents, source_digest)

    def compact(self) -> None:
        if self.path.exists():
            with closing(self._open()) as db:
                db.execute("VACUUM")
