"""Bounded, disposable recall over current agent-authored Markdown.

Semantic paths remain the authority. Reconciliation happens before every query,
so direct edits, moves and deletes are reflected without relying on write hooks.
The index stores lexical terms/vectors, never an authoritative copy of the prose.

Recall reads four tiers of knowledge: this project's Wiki and Skills, the
shared Wiki of the project's vertical (with its ``principles.md``), the
host-wide shared Wiki, and -- on a single-operator host -- the Wikis of earlier
projects of the same vertical. Every shown page is described (title,
description, kind, source, date) so a role can decide whether to open it, and
every recall is noted in the knowledge journal and the project event stream.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import os
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..core.event_catalog import EventType
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
_PAGE_KINDS = ("fact", "lesson", "survey", "principles", "page")
_SCOPES = ("project", "vertical", "global")
_DESCRIPTION_CHARS = 160
_FRONT_MATTER_CHARS = 4000
_MAX_SIBLING_WIKIS = 8
_MAX_SIBLING_WORKSPACES = 64
_MAX_WORKSPACE_ENTRIES = 256
_OBJECTIVE_EXCERPT_CHARS = 200
_SIBLING_WIKIS_KNOB = "ARGUS_SKILL_RECALL_SIBLING_WIKIS"
# Read through the knob resolver rather than the hosted-trial client: the
# runtime layer may not import the delivery layer, and both read this switch.
_HOSTED_TRIAL_KNOB = "ARGUS_SKILL_COPILOT_TRIAL"
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_RECALL_HEADER = "### What Argus already knows (advisory)"
_RECALL_INTRO = (
    "These pages come from the current knowledge library and match this objective. "
    "Open a page before relying on it and check its evidence and scope; similarity "
    "grants no authority. An edited or removed page drops out on the next recall."
)


@dataclass(frozen=True)
class KnowledgeRoot:
    kind: str
    path: Path
    boundary: Path
    # Where the pages sit in the knowledge library: this project, the
    # project's vertical, or the whole host. Journal records carry it.
    scope: str = "project"
    vertical: str = ""
    # Journal paths are relative to the library root (the Wiki directory that
    # holds ``pages/``), not to the scanned ``pages`` directory.
    library: Path | None = None
    # Shown as the page's origin when its front matter names no source.
    source: str = ""


@dataclass(frozen=True)
class PageMeta:
    title: str = ""
    description: str = ""
    page_kind: str = "page"
    source: str = ""
    created: str = ""
    body_lead: str = ""


@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    path: Path
    kind: str
    digest: str
    content: str
    root: KnowledgeRoot | None = None
    meta: PageMeta = PageMeta()

    def indexed(self) -> RecallDocument:
        # Markdown has no monotonic source revision: this is a content token,
        # while the full digest is checked independently by the derived index.
        return RecallDocument(
            self.id, int(self.digest[:15], 16), self.digest,
            f"{self.path.name}\n{self.content[:1600]}", self.content,
        )

    @property
    def scope(self) -> str:
        return self.root.scope if self.root is not None else "project"

    @property
    def vertical(self) -> str:
        return self.root.vertical if self.root is not None else ""

    @property
    def relative_path(self) -> str:
        """The page's path inside its library, as the knowledge journal records it."""
        if self.root is None:
            return self.path.name
        library = (self.root.library or self.root.path).absolute()
        try:
            return self.path.absolute().relative_to(library).as_posix()
        except ValueError:
            return self.path.name

    @property
    def origin(self) -> str:
        """The source project named by the page, else the tier it was read from."""
        if self.meta.source:
            return self.meta.source
        if self.root is not None and self.root.source:
            return self.root.source
        if self.scope == "vertical" and self.vertical:
            return self.vertical
        return self.scope


@dataclass(frozen=True)
class RecallHit:
    document: KnowledgeDocument
    channel: str
    line: str


@dataclass(frozen=True)
class RecallResult:
    text: str
    hits: tuple[RecallHit, ...] = ()
    principles: RecallHit | None = None

    @property
    def shown(self) -> tuple[RecallHit, ...]:
        return (*self.hits, *((self.principles,) if self.principles is not None else ()))


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


def _clip(text: str, limit: int = _DESCRIPTION_CHARS) -> str:
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _front_matter(content: str) -> tuple[dict[str, Any], str]:
    """The page's front matter mapping and body; an empty mapping when absent."""
    if not content.startswith("---\n"):
        return {}, content
    front, separator, body = content[4:].partition("\n---\n")
    if not separator or len(front) > _FRONT_MATTER_CHARS:
        return {}, content
    try:
        loaded = yaml.safe_load(front)
    except yaml.YAMLError:
        return {}, content
    return (loaded if isinstance(loaded, dict) else {}), body


def _first_prose_line(body: str) -> str:
    """First prose line, skipping headings, fences and table rows."""
    fenced = False
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not line or line.startswith(("#", "|", "---")):
            continue
        return line
    return ""


def _first_heading(body: str) -> str:
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _iso_date(value: Any, fallback_mtime: float) -> str:
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    text = str(value or "").strip()
    if _ISO_DATE_RE.fullmatch(text[:10]):
        return text[:10]
    try:
        return _dt.date.fromtimestamp(fallback_mtime).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _describe(root: KnowledgeRoot, path: Path, content: str, mtime: float) -> PageMeta:
    front, body = _front_matter(content)
    title = str(front.get("title") or front.get("name") or "").strip() or _first_heading(body) or path.stem
    description = str(front.get("description") or "").strip() or _first_prose_line(body)
    kind = str(front.get("kind") or "").strip().lower()
    if kind not in _PAGE_KINDS:
        if path.name.casefold() == "principles.md" or root.kind == "principles":
            kind = "principles"
        elif "skill" in root.kind.casefold():
            kind = "skill"
        else:
            kind = "page"
    return PageMeta(
        title=_clip(title),
        description=_clip(description),
        page_kind=kind,
        source=_clip(str(front.get("source") or "").strip(), 80),
        created=_iso_date(front.get("created"), mtime),
        body_lead=_clip(_first_prose_line(body)),
    )


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


def _sibling_wikis_enabled() -> bool:
    """Other projects' Wikis are read only on a single-operator host that keeps the switch on."""
    try:
        from ..core.knobs import resolve_knob

        if resolve_knob(_HOSTED_TRIAL_KNOB, "0").value.strip() == "1":
            return False
        return resolve_knob(_SIBLING_WIKIS_KNOB, "1").value.strip() == "1"
    except Exception:  # noqa: BLE001 - an unreadable switch means no cross-project reads
        return False


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left == right or left.resolve() == right.resolve()
    except (OSError, RuntimeError):
        return False


def _sibling_wiki_roots(global_root: Path, workspace: Path, vertical: str) -> list[tuple[str, Path]]:
    """Wikis of earlier workspaces whose persisted state names the same vertical, newest first."""
    if not vertical:
        return []
    workspaces = global_root / "workspaces"
    if not _contained(workspaces, global_root) or not workspaces.is_dir():
        return []
    candidates: list[tuple[float, Path]] = []
    try:
        with os.scandir(workspaces) as entries:
            for _ in range(_MAX_WORKSPACE_ENTRIES):
                try:
                    entry = next(entries)
                except StopIteration:
                    break
                if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
                candidate = workspaces / entry.name
                if _same_path(candidate, workspace):
                    continue
                state = candidate / ".argus" / "PIPELINE_STATE.json"
                try:
                    if state.is_symlink() or not state.is_file():
                        continue
                    modified = state.stat().st_mtime
                except OSError:
                    continue
                candidates.append((modified, candidate))
    except OSError:
        return []
    candidates.sort(key=lambda row: (-row[0], str(row[1])))
    from ..skills.vertical_select import resolve_skill_scope

    roots: list[tuple[str, Path]] = []
    for _modified, candidate in candidates[:_MAX_SIBLING_WORKSPACES]:
        try:
            if resolve_skill_scope(candidate) != vertical:
                continue
        except Exception:  # noqa: BLE001 - another project's state is read, never repaired
            continue
        for wiki in _bounded_wiki_roots(candidate):
            roots.append((wiki.parent.name, wiki))
            if len(roots) >= _MAX_SIBLING_WIKIS:
                return roots
    return roots


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

    def _load(self, source: KnowledgeRoot, path: Path, root: Path, size: int, mtime: float,
              remaining_bytes: int) -> tuple[KnowledgeDocument | None, int]:
        budget = min(self.max_file_bytes, remaining_bytes)
        # Refuse oversized files instead of indexing a truncated claim whose
        # scope or qualification may occur later.
        if budget <= 0 or size > budget:
            return None, remaining_bytes
        raw = _read_current_file(path, budget)
        remaining_bytes -= len(raw)
        if len(raw) > budget or not _contained(path, root):
            return None, remaining_bytes
        content = raw.decode("utf-8")
        if not content.strip():
            return None, remaining_bytes
        digest = hashlib.sha256(raw).hexdigest()
        identity = "markdown:" + hashlib.sha256(str(path).encode()).hexdigest()
        document = KnowledgeDocument(
            identity, path, source.kind, digest, content, root=source,
            meta=_describe(source, path, content, mtime),
        )
        return document, remaining_bytes

    def _snapshot(self) -> list[KnowledgeDocument]:
        documents: list[KnowledgeDocument] = []
        seen: set[Path] = set()
        remaining_bytes = self.max_total_bytes
        remaining_entries = self.max_scan_entries
        for source in self.roots:
            root, boundary = source.path.absolute(), source.boundary.absolute()
            if not _contained(root, boundary) or not _contained(boundary, boundary):
                continue
            if len(documents) >= self.max_documents:
                break
            try:
                status = root.lstat()
            except OSError:
                continue
            if not os.path.isdir(root) or os.path.islink(root):
                # A single page (the vertical's principles.md) is a root of its own.
                if (os.path.islink(root) or not os.path.isfile(root) or root.suffix.casefold() != ".md"
                        or root in seen):
                    continue
                try:
                    document, remaining_bytes = self._load(
                        source, root, root, status.st_size, status.st_mtime, remaining_bytes,
                    )
                except (OSError, UnicodeError, ValueError):
                    continue
                if document is not None:
                    documents.append(document)
                    seen.add(root)
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
                        if min(self.max_file_bytes, remaining_bytes) <= 0:
                            break
                        status = entry.stat(follow_symlinks=False)
                        document, remaining_bytes = self._load(
                            source, path, root, status.st_size, status.st_mtime, remaining_bytes,
                        )
                        if document is None:
                            continue
                        documents.append(document)
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
        return self.recall(objective, max_entries=max_entries, max_chars=max_chars).text

    def recall(self, objective: str, *, max_entries: int = 4, max_chars: int = 2000) -> RecallResult:
        """Render the matching pages and return them with the text, for the journal."""
        with _embedding_batch(self.index.embedder):
            return self._recall(objective, max_entries=max_entries, max_chars=max_chars)

    @staticmethod
    def _line(document: KnowledgeDocument, channel: str) -> str:
        meta = document.meta
        details = [meta.page_kind, document.origin]
        if meta.created:
            details.append(meta.created)
        tokens = [f"content {document.digest[:12]}"]
        if channel:
            tokens.append(channel)
        summary = meta.description or meta.title
        return (
            f"- {document.kind}: `{document.path}` — {summary} "
            f"({'; '.join(details)}) [{'; '.join(tokens)}]"
        )

    def _recall(self, objective: str, *, max_entries: int, max_chars: int) -> RecallResult:
        if max_entries <= 0 or max_chars <= 0:
            return RecallResult("")
        documents = self._snapshot()
        indexed, digest, available = self._synchronize(documents)
        if not documents or not objective.strip():
            return RecallResult("")
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
            return RecallResult("")
        principles = next(
            (document for document in documents
             if document.root is not None and document.root.kind == "principles"), None,
        )
        candidates = [document for document in documents if document is not principles]

        def rank(document: KnowledgeDocument) -> tuple:
            score = scores[document.id]
            # A lesson learned from an earlier mission outranks a plain page
            # that matches equally well: it carries a boundary, not only facts.
            return (-score.direct, -score.transfer, document.meta.page_kind != "lesson",
                    -score.vector, str(document.path))

        matching = sorted(
            (document for document in candidates
             if scores[document.id].direct or scores[document.id].transfer),
            key=rank,
        )
        limit = min(4, max_entries)
        hits = matching[:limit]
        if hits and not any(document.meta.page_kind == "lesson" for document in hits):
            lesson = next((document for document in matching if document.meta.page_kind == "lesson"), None)
            if lesson is not None:
                hits = hits[:max(0, limit - 1)] + [lesson]
        if candidates and not self.index.embedder.identifier.startswith("lexical-hash-"):
            semantic = max(candidates, key=lambda document: scores[document.id].vector)
            if scores[semantic.id].vector > 0 and semantic not in hits:
                hits = hits[:max(0, limit - 1)] + [semantic]
        if not hits and principles is None:
            return RecallResult("")
        lines = [_RECALL_HEADER, _RECALL_INTRO]
        shown: list[RecallHit] = []
        for document in hits:
            # Keep context as discovery pointers. Full prose is read by the
            # agent from the current semantic path, preserving its boundaries.
            score = scores[document.id]
            channel = "" if score.direct or score.transfer else "embedding similarity (advisory)"
            line = self._line(document, channel)
            lines.append(line)
            shown.append(RecallHit(document, channel or "lexical match", line))
        principles_hit: RecallHit | None = None
        if principles is not None:
            lead = principles.meta.body_lead or principles.meta.description or principles.meta.title
            line = f"- principles: `{principles.path}` — {lead}"
            lines.append(line)
            principles_hit = RecallHit(principles, "principles", line)
        text = ("\n".join(lines) + "\n")[:max_chars]
        # A tight budget can cut the tail: only fully shown lines count as recalled.
        kept = tuple(hit for hit in shown if hit.line + "\n" in text)
        if principles_hit is not None and principles_hit.line + "\n" not in text:
            principles_hit = None
        return RecallResult(text, kept, principles_hit)


def _resolve_vertical(workspace: Path, life_dir: Path | None = None) -> str:
    from ..skills.vertical_select import resolve_project_vertical

    try:
        return str(resolve_project_vertical(workspace, life_dir=life_dir) or "").strip()
    except Exception:  # noqa: BLE001 - recall informs; an undecided vertical only narrows the roots
        return ""


def _shared_wiki_roots(global_root: Path, vertical: str) -> list[KnowledgeRoot]:
    """The vertical's shared Wiki (pages and principles) and the host-wide shared Wiki."""
    from ..core.paths import global_wiki_root, shared_vertical_wiki_root, shared_wiki_root

    roots: list[KnowledgeRoot] = []
    try:
        boundary = shared_wiki_root(global_root).absolute()
        if vertical:
            vertical_root = shared_vertical_wiki_root(vertical, global_root).absolute()
            roots.append(KnowledgeRoot(
                f"{vertical} knowledge", vertical_root / "pages", boundary,
                scope="vertical", vertical=vertical, library=vertical_root,
            ))
            roots.append(KnowledgeRoot(
                "principles", vertical_root / "principles.md", boundary,
                scope="vertical", vertical=vertical, library=vertical_root,
            ))
        global_pages = global_wiki_root(global_root).absolute()
        roots.append(KnowledgeRoot(
            "shared knowledge", global_pages / "pages", boundary,
            scope="global", library=global_pages,
        ))
    except (OSError, ValueError):
        return []
    return roots


def knowledge_recall_for_memory(memory: Any, *, worktree: Path | None = None,
                                skill_store: Any = None,
                                index_path: Path | None = None) -> MarkdownKnowledgeRecall:
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
        roots.append(KnowledgeRoot("shared Skill", global_root / "skills", global_root, scope="global"))
    if skill_store is not None:
        for root in skill_store.library_roots():
            path = Path(root).absolute()
            roots.append(KnowledgeRoot("configured Skill", path, path, scope="global"))
    vertical = ""
    if workspace is not None:
        workspace = Path(workspace).absolute()
        if _contained(workspace, workspace):
            vertical = _resolve_vertical(workspace, state)
            roots.append(KnowledgeRoot("native project Skill", workspace / ".agents" / "skills", workspace))
            for wiki in _bounded_wiki_roots(workspace):
                roots.insert(0, KnowledgeRoot("project Wiki", wiki / "pages", workspace, library=wiki))
            if global_root is not None and vertical:
                from ..skills.layered import shared_skill_scope_dir

                scope = shared_skill_scope_dir(global_root / "skills", vertical)
                if scope is not None:
                    roots.append(KnowledgeRoot(
                        "scoped shared Skill", scope, global_root, scope="vertical", vertical=vertical,
                    ))
    if global_root is not None:
        # Shared tiers follow the project's own pages and precede the Skills,
        # so they keep their share of the bounded document budget.
        project_count = sum(1 for root in roots if root.kind == "project Wiki")
        roots[project_count:project_count] = _shared_wiki_roots(global_root, vertical)
        if workspace is not None and vertical and _sibling_wikis_enabled():
            for sid, wiki in _sibling_wiki_roots(global_root, workspace, vertical):
                roots.append(KnowledgeRoot(
                    f"earlier project Wiki ({sid})", wiki / "pages", global_root,
                    scope="project", vertical=vertical, library=wiki, source=sid,
                ))
    # A subordinate can keep its derived index isolated while using the same
    # explicit canonical source and embedding policy/budget as its parent.
    from .recall_embedding import configured_embedder

    return MarkdownKnowledgeRecall(
        index_path if index_path is not None else state / "knowledge-recall.sqlite3", roots,
        embedder=configured_embedder(state),
    )


def _recall_global_root(memory: Any) -> Path | None:
    configured = getattr(memory, "global_root", None)
    if configured is not None:
        return Path(configured).expanduser().absolute()
    root = getattr(memory, "project_root", None)
    if root is None:
        root = getattr(memory, "root", None)
    if root is None:
        return None
    root = Path(root).expanduser().absolute()
    return root.parent.parent if root.parent.name == "projects" else None


def record_recall(memory: Any, result: RecallResult, *, objective: str, role: str = "engineer",
                  mission_id: str = "", life_dir: Path | None = None) -> None:
    """Note what was recalled: one journal record per page, one event per prompt. Never raises."""
    shown = result.shown
    if not shown:
        return
    role = str(role or "engineer").strip() or "engineer"
    mission_id = str(mission_id or "").strip()
    global_root = _recall_global_root(memory)
    if global_root is not None:
        appender: Callable[..., Any] | None
        try:
            from ..wiki.journal import append_knowledge_event as appender
        except ImportError:
            appender = None
        if appender is not None:
            for hit in shown:
                document = hit.document
                try:
                    appender(
                        global_root, kind="recalled", scope=document.scope, vertical=document.vertical,
                        path=document.relative_path, title=document.meta.title,
                        source_project=document.origin if document.meta.source or (
                            document.root is not None and document.root.source) else "",
                        mission_id=mission_id, role=role, page_kind=document.meta.page_kind,
                        note=f"shown to the {role} via {hit.channel}",
                    )
                except Exception:  # noqa: BLE001 - the journal is a record, never a requirement
                    log.debug("knowledge journal append failed", exc_info=True)
    if life_dir is None:
        life_dir = getattr(memory, "project_root", None)
    if life_dir is None:
        life_dir = getattr(memory, "root", None)
    if life_dir is None:
        return
    try:
        from .event_log import JsonlEventSink

        scope_counts = Counter(hit.document.scope for hit in shown)
        event: dict[str, Any] = {
            "type": EventType.KNOWLEDGE_RECALLED,
            "role": role,
            "paths": [str(hit.document.path) for hit in shown],
            "scope_counts": dict(sorted(scope_counts.items())),
            "objective_excerpt": " ".join(str(objective or "").split())[:_OBJECTIVE_EXCERPT_CHARS],
            "text": f"Recalled {len(shown)} knowledge page(s) for the {role}",
        }
        if mission_id:
            event["mission_id"] = mission_id
        JsonlEventSink(None, life_dir=Path(life_dir)).append(event)
    except Exception:  # noqa: BLE001 - the event stream is a record, never a requirement
        log.debug("knowledge recall event append failed", exc_info=True)


def render_memory_recall(memory: Any, objective: str, *, max_entries: int = 4,
                         max_chars: int = 6000,
                         knowledge_index_path: Path | None = None,
                         role: str = "engineer", mission_id: str = "",
                         life_dir: Path | None = None) -> str:
    if max_entries <= 0 or max_chars <= 0:
        return ""
    knowledge = ""
    try:
        result = knowledge_recall_for_memory(memory, index_path=knowledge_index_path).recall(
            objective, max_entries=max_entries, max_chars=min(2000, max_chars // 3),
        )
        knowledge = result.text
        record_recall(memory, result, objective=objective, role=role, mission_id=mission_id, life_dir=life_dir)
    except Exception:  # noqa: BLE001 - optional recall must not own mission execution
        log.warning("knowledge recall unavailable", exc_info=True)
    experiences = memory.render_failure_experience_context(
        objective, max_entries=max_entries, max_chars=max(0, max_chars - len(knowledge) - bool(knowledge)),
    )
    return "\n".join(block for block in (experiences, knowledge) if block)[:max_chars]
