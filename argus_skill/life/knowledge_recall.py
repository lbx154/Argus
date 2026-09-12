"""Bounded, disposable recall over current agent-authored Markdown.

Semantic paths remain the authority. Reconciliation happens before every query,
so direct edits, moves and deletes are reflected without relying on write hooks.
The index stores lexical terms/vectors, never an authoritative copy of the prose.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.scoped_file import open_regular_file
from .failure_experience_index import (
    EmbeddingAdapter,
    EmbeddingUnavailable,
    FailureExperienceIndex,
    RecallDocument,
    _embedding_batch,
    lexical_scores,
)

log = logging.getLogger(__name__)
_EXCLUDED = {"_archive", "_history", "_retired", "_shared_verticals"}


@dataclass(frozen=True)
class KnowledgeRoot:
    kind: str
    path: Path
    boundary: Path


@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    path: Path
    kind: str
    digest: str
    content: str

    def indexed(self) -> RecallDocument:
        # Markdown has no monotonic source revision: this is a content token,
        # while the full digest is checked independently by the derived index.
        return RecallDocument(
            self.id, int(self.digest[:15], 16), self.digest,
            f"{self.path.name}\n{self.content[:1600]}", self.content,
        )


def _contained(path: Path, boundary: Path) -> bool:
    try:
        return path.resolve() == path and path.is_relative_to(boundary)
    except (OSError, RuntimeError):
        return False


def _read_current_file(path: Path, budget: int) -> bytes:
    """Read a regular file without following a replaced parent link on POSIX."""
    with open_regular_file(path) as handle:
        before = os.fstat(handle.fileno())
        if before.st_size > budget:
            raise ValueError("knowledge file is not a bounded regular file")
        raw = handle.read(budget + 1)
        after = os.fstat(handle.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns,
    ):
        raise ValueError("knowledge file changed during read")
    return raw


def _bounded_wiki_roots(workspace: Path) -> list[Path]:
    """Discover a finite set without materializing an unbounded .autors tree."""
    autors = workspace / ".autors"
    if not _contained(autors, workspace):
        return []
    roots: list[Path] = []
    try:
        with os.scandir(autors) as entries:
            for _ in range(256):
                try:
                    entry = next(entries)
                except StopIteration:
                    break
                if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
                wiki = autors / entry.name / "wiki"
                paths = (wiki, wiki / "pages", wiki / "INDEX.md")
                if all(_contained(path, workspace) for path in paths) and paths[1].is_dir() and paths[2].is_file():
                    roots.append(wiki)
                    if len(roots) >= 16:
                        break
    except OSError:
        pass
    return sorted(roots)


class MarkdownKnowledgeRecall:
    def __init__(self, index_path: Path, roots: list[KnowledgeRoot], *,
                 max_documents: int = 256, max_file_bytes: int = 32_768,
                 max_total_bytes: int = 1_000_000, max_scan_entries: int = 4096,
                 embedder: EmbeddingAdapter | None = None) -> None:
        from .recall_embedding import configured_embedder

        self.index = FailureExperienceIndex(
            index_path, embedder=embedder or configured_embedder(index_path.parent),
        )
        self.roots = roots[:32]
        self.max_documents = max(0, min(1024, max_documents))
        self.max_file_bytes = max(0, min(131_072, max_file_bytes))
        self.max_total_bytes = max(0, min(4_000_000, max_total_bytes))
        self.max_scan_entries = max(0, min(16_384, max_scan_entries))

    def _snapshot(self) -> list[KnowledgeDocument]:
        documents: list[KnowledgeDocument] = []
        seen: set[Path] = set()
        remaining_bytes = self.max_total_bytes
        remaining_entries = self.max_scan_entries
        for source in self.roots:
            root, boundary = source.path.absolute(), source.boundary.absolute()
            if not _contained(root, boundary) or not _contained(boundary, boundary):
                continue
            pending = [(root, 0)]
            while pending and remaining_entries > 0 and len(documents) < self.max_documents:
                directory, depth = pending.pop()
                if depth > 16 or not _contained(directory, boundary):
                    continue
                try:
                    with os.scandir(directory) as iterator:
                        entries = []
                        for entry in iterator:
                            if remaining_entries <= 0:
                                break
                            remaining_entries -= 1
                            entries.append(entry)
                    for entry in sorted(entries, key=lambda row: row.name):
                        if entry.name.startswith(".") or entry.name in _EXCLUDED or entry.is_symlink():
                            continue
                        path = Path(entry.path)
                        if entry.is_dir(follow_symlinks=False):
                            pending.append((path, depth + 1))
                            continue
                        if (path.suffix.casefold() != ".md" or path.name.casefold() == "index.md"
                                or path in seen or not entry.is_file(follow_symlinks=False)
                                or len(documents) >= self.max_documents or not _contained(path, root)):
                            continue
                        budget = min(self.max_file_bytes, remaining_bytes)
                        if budget <= 0:
                            break
                        # Refuse oversized files instead of indexing a truncated
                        # claim whose scope or qualification may occur later.
                        if entry.stat(follow_symlinks=False).st_size > budget:
                            continue
                        raw = _read_current_file(path, budget)
                        remaining_bytes -= len(raw)
                        if len(raw) > budget or not _contained(path, root):
                            continue
                        content = raw.decode("utf-8")
                        if not content.strip():
                            continue
                        digest = hashlib.sha256(raw).hexdigest()
                        identity = "markdown:" + hashlib.sha256(str(path).encode()).hexdigest()
                        documents.append(KnowledgeDocument(identity, path, source.kind, digest, content))
                        seen.add(path)
                except (OSError, UnicodeError, ValueError):
                    # Inaccessible or changed canonical files have no current
                    # index entry; never serve their previous cached version.
                    continue
        return documents

    def _synchronize(self, documents: list[KnowledgeDocument]) -> tuple[list[RecallDocument], str, bool]:
        indexed = [document.indexed() for document in documents]
        digest = hashlib.sha256("\n".join(
            f"{document.id}:{document.digest}" for document in sorted(documents, key=lambda row: row.id)
        ).encode()).hexdigest()
        try:
            self.index.sync(indexed, digest)
            return indexed, digest, True
        except EmbeddingUnavailable:
            log.warning("knowledge embedding unavailable; using current lexical recall")
            return indexed, digest, False
        except Exception:
            log.warning("knowledge index sync failed; rebuilding current Markdown", exc_info=True)
        try:
            self.index.rebuild(indexed, digest)
            return indexed, digest, True
        except Exception:
            log.warning("knowledge index unavailable; recalling current Markdown only", exc_info=True)
            return indexed, digest, False

    def sync(self) -> dict[str, int]:
        documents = self._snapshot()
        _, _, available = self._synchronize(documents)
        return {"documents": len(documents), "indexed": int(available)}

    def render_context(self, objective: str, *, max_entries: int = 4, max_chars: int = 2000) -> str:
        with _embedding_batch(self.index.embedder):
            return self._render_context(objective, max_entries=max_entries, max_chars=max_chars)

    def _render_context(self, objective: str, *, max_entries: int, max_chars: int) -> str:
        if max_entries <= 0 or max_chars <= 0:
            return ""
        documents = self._snapshot()
        indexed, digest, available = self._synchronize(documents)
        if not documents or not objective.strip():
            return ""
        scores = lexical_scores(indexed, objective)
        if available:
            try:
                indexed_scores = self.index.scores(objective, source_digest=digest)
                if set(indexed_scores) != {document.id for document in documents}:
                    raise ValueError("knowledge index document identities changed during recall")
                scores = indexed_scores
            except EmbeddingUnavailable:
                log.warning("knowledge query embedding unavailable; using current lexical recall")
                documents = self._snapshot()
                scores = lexical_scores([document.indexed() for document in documents], objective)
            except Exception:
                log.warning("knowledge query failed; using current lexical recall", exc_info=True)
                # Canonical files can change while an optional remote query is
                # running. Refresh once, then use local scores without retrying
                # the embedding or presenting paths from the obsolete snapshot.
                documents = self._snapshot()
                scores = lexical_scores([document.indexed() for document in documents], objective)
        if not documents:
            return ""
        hits = sorted(
            (document for document in documents
             if scores[document.id].direct or scores[document.id].transfer),
            key=lambda document: (
                -scores[document.id].direct, -scores[document.id].transfer,
                -scores[document.id].vector, str(document.path),
            ),
        )[:min(4, max_entries)]
        if not self.index.embedder.identifier.startswith("lexical-hash-"):
            semantic = max(documents, key=lambda document: scores[document.id].vector)
            if scores[semantic.id].vector > 0 and semantic not in hits:
                hits = hits[:max(0, min(4, max_entries) - 1)] + [semantic]
        if not hits:
            return ""
        lines = [
            "### Current Wiki and Skill pointers (advisory)",
            "These matches come from current canonical Markdown. Open the page before reuse; "
            "check its evidence, scope and uncertainty. Similarity grants no authority. "
            "An edited or removed page replaces its previous index entry on the next recall.",
        ]
        for document in hits:
            # Keep context as discovery pointers. Full prose is read by the
            # agent from the current semantic path, preserving its boundaries.
            score = scores[document.id]
            channel = "lexical match" if score.direct or score.transfer else "embedding similarity (advisory)"
            lines.append(f"- {document.kind}: `{document.path}` (content {document.digest[:12]}; {channel})")
        return ("\n".join(lines) + "\n")[:max_chars]


def knowledge_recall_for_memory(memory: Any, *, worktree: Path | None = None,
                                skill_store: Any = None) -> MarkdownKnowledgeRecall:
    state = getattr(memory, "project_root", None)
    if state is None:
        state = getattr(memory, "root", None)
    if state is None:
        raise ValueError("knowledge recall requires an explicit project state root")
    state = Path(state).absolute()
    workspace = worktree if worktree is not None else getattr(memory, "project_worktree", None)
    roots: list[KnowledgeRoot] = [KnowledgeRoot("project Skill", state / "skills", state)]
    global_root = getattr(memory, "global_root", None)
    if global_root is not None:
        global_root = Path(global_root).absolute()
        roots.append(KnowledgeRoot("shared Skill", global_root / "skills", global_root))
    if skill_store is not None:
        for root in skill_store.library_roots():
            path = Path(root).absolute()
            roots.append(KnowledgeRoot("configured Skill", path, path))
    if workspace is not None:
        workspace = Path(workspace).absolute()
        if _contained(workspace, workspace):
            roots.append(KnowledgeRoot("native project Skill", workspace / ".agents" / "skills", workspace))
            for wiki in _bounded_wiki_roots(workspace):
                roots.insert(0, KnowledgeRoot("project Wiki", wiki / "pages", workspace))
            if global_root is not None:
                from ..skills.layered import shared_skill_scope_dir
                from ..skills.vertical_select import resolve_skill_scope

                scope = shared_skill_scope_dir(global_root / "skills", resolve_skill_scope(workspace))
                if scope is not None:
                    roots.append(KnowledgeRoot("scoped shared Skill", scope, global_root))
    return MarkdownKnowledgeRecall(state / "knowledge-recall.sqlite3", roots)


def render_memory_recall(memory: Any, objective: str, *, max_entries: int = 4,
                         max_chars: int = 6000) -> str:
    if max_entries <= 0 or max_chars <= 0:
        return ""
    knowledge = ""
    try:
        knowledge = knowledge_recall_for_memory(memory).render_context(
            objective, max_entries=max_entries, max_chars=min(2000, max_chars // 3),
        )
    except Exception:  # noqa: BLE001 - optional recall must not own mission execution
        log.warning("knowledge recall unavailable", exc_info=True)
    experiences = memory.render_failure_experience_context(
        objective, max_entries=max_entries, max_chars=max(0, max_chars - len(knowledge) - bool(knowledge)),
    )
    return "\n".join(block for block in (experiences, knowledge) if block)[:max_chars]
