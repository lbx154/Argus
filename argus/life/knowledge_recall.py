"""Bounded, disposable recall over current agent-authored Markdown.

Semantic paths remain the authority. Reconciliation happens before every query,
so direct edits, moves and deletes are reflected without relying on write hooks.
The index stores lexical terms/vectors, never an authoritative copy of the prose.

Recall reads four tiers of knowledge: this project's Wiki and Skills, the
shared Wiki of the project's vertical (with its ``principles.md``), the
host-wide shared Wiki, and -- on a single-operator host -- the Wikis and
Skills of earlier projects. Other verticals' shared Wikis and earlier projects
of any vertical take part like the project's own: what matches the work is
shown, whatever field it was learned in. Every shown page is described (title, description, kind, source, date, and the
code it carries) so a role can decide whether to open it, and every recall is
noted in the knowledge journal and the project event stream.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any

import yaml

from ..core.event_catalog import EventType
from ..core.file_facts import file_facts
from ..core.paths import NATIVE_SKILLS_DIR
from ..core.scoped_file import open_regular_file
from ..skills.layered import skill_code_files
from .failure_experience_index import (
    EmbeddingAdapter,
    EmbeddingUnavailable,
    FailureExperienceIndex,
    RecallDocument,
    _embedding_batch,
    is_lexical,
    lexical_scores,
)

log = logging.getLogger(__name__)
_EXCLUDED = {"_archive", "_history", "_retired", "_shared_verticals"}
_PAGE_KINDS = ("fact", "lesson", "survey", "principles", "note", "profile", "page")
_SCOPES = ("project", "vertical", "global")
_DESCRIPTION_CHARS = 160
_FRONT_MATTER_CHARS = 4000
_MAX_CODE_FILES = 4
_MAX_PAGE_BYTES = 32_768
_MAX_CITING_PAGES = 64
_MAX_SIBLINGS = 8
_MAX_SIBLING_CANDIDATES = 64
_MAX_PROJECT_ENTRIES = 256
_OBJECTIVE_EXCERPT_CHARS = 200
# Read through the knob resolver rather than the hosted-trial client: the
# runtime layer may not import the delivery layer, and both read this switch.
_HOSTED_TRIAL_KNOB = "ARGUS_SKILL_COPILOT_TRIAL"
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_RECALL_HEADER = "### What Argus already knows (advisory)"
# These terms express a request or page scaffolding, not a topic match. Keep
# them out of lexical scores without degrading the text used by embeddings.
_SEARCH_VERSION = "knowledge-content-v2"
_GENERIC_TERMS = frozenset("""
a an and are as at be been but by can could did do does for from had has have
how if in into is it its may not of on or our should that the their them then
there these they this those to us was we were what when where which who why
will with would you your please help explain tell learn learning knowledge
question answer source sources survey created description title confidence
请问 请教 谢谢 你好 学习 知识 问题 回答 解释 告诉 这个 那个 什么 怎么 如何
我们 你们 可以 需要 一些 一下 进行 关于 相关 然后
""".split())
_PROVENANCE_SECTIONS = frozenset({"question", "the question", "history", "问题", "提问", "历史", "历史记录"})
_SEMANTIC_MIN_SIMILARITY = 0.5
_RECALL_INTRO = (
    "These pages come from the current knowledge library and match this objective. "
    "Open a page before relying on it and check its evidence and scope; similarity "
    "grants no authority. A page holds for the date, source and version it names; when "
    "current facts contradict one, say so with its path and why, so it can be corrected "
    "or retired. An edited or removed page drops out on the next recall."
)
_CODE_NOTE = (" Code listed with a page is relative to it; the page says how to run it, and "
              "rerunning it shows whether the page still holds.")


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
    # The date the page was last corrected in place, read from its History entries.
    corrected: str = ""
    # Files of code the page carries, relative to the page's directory.
    code: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    path: Path
    kind: str
    # The page's ``file_facts`` when read; a rewrite is a new version.
    version: str
    content: str
    root: KnowledgeRoot | None = None
    meta: PageMeta = PageMeta()

    def indexed(self) -> RecallDocument:
        # Markdown has no monotonic source revision; the version also names
        # the searchable projection, so changing it rebuilds old caches even
        # when the canonical page bytes are unchanged.
        body = searchable_body(self.content)
        return RecallDocument(
            self.id, 0, f"{_SEARCH_VERSION}:{self.version}",
            f"{self.path.stem}\n{self.meta.title}\n{self.meta.description}\n{body[:1600]}",
            body, _GENERIC_TERMS,
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
    # Normalize only this parsing copy. The stored content, version and byte
    # budget still describe the original file exactly.
    content = content.replace("\r\n", "\n")
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


def searchable_body(content: str) -> str:
    """Search current knowledge and procedures, excluding provenance scaffolding."""
    _front, body = _front_matter(content)
    kept: list[str] = []
    excluded_depth = 0
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        heading = None if fenced else re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            depth = len(heading[1])
            if excluded_depth and depth <= excluded_depth:
                excluded_depth = 0
            if heading[2].strip().casefold() in _PROVENANCE_SECTIONS:
                excluded_depth = depth
            # Titles and descriptions have their own stronger matching field.
            continue
        if not excluded_depth:
            kept.append(line)
    return "\n".join(kept)


_CORRECTION_ENTRY_RE = re.compile(r"^\s*[-*]\s+(\d{4}-\d{2}-\d{2}),\s+corrected by\b")


def _corrected_date(body: str) -> str:
    """The latest ``- <date>, corrected by ...`` entry under the page's History heading.

    The correction lives in the body rather than the front matter, so a page
    rewritten through the two-field page format keeps it.
    """
    latest = ""
    in_history = False
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        heading = None if fenced else re.match(r"^#{1,6}\s+(.+)$", line)
        if heading:
            in_history = heading[1].strip().casefold() == "history"
            continue
        match = _CORRECTION_ENTRY_RE.match(line) if in_history and not fenced else None
        if match and match[1] > latest:
            latest = match[1]
    return latest


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


def _code_files(root: KnowledgeRoot, path: Path, body: str) -> tuple[str, ...]:
    """The code a page carries: files beside a ``SKILL.md``, else the files of its library it links to.

    Only paths are listed; the page says how to run them and the reader
    decides whether to.
    """
    from ..wiki.promote import linked_files

    directory = path.parent
    boundary = (root.library or root.path).absolute()
    if path.name == "SKILL.md":
        candidates = [directory / name for name in skill_code_files(path)]
    else:
        candidates = linked_files(path, body, boundary)
    found: list[str] = []
    for candidate in candidates:
        candidate = Path(os.path.normpath(candidate))
        relative = Path(os.path.relpath(candidate, directory)).as_posix()
        if (candidate.suffix.casefold() == ".md" or relative in found
                or any(part.startswith(".") and part != ".." or part == "__pycache__"
                       for part in relative.split("/"))):
            continue
        try:
            if candidate.is_symlink() or not candidate.is_file() or not _contained(candidate, boundary):
                continue
        except OSError:
            continue
        found.append(relative)
        if len(found) >= _MAX_CODE_FILES:
            break
    return tuple(found)


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
        corrected=_corrected_date(body),
        code=_code_files(root, path, body),
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


def _sibling_libraries_enabled() -> bool:
    """Other projects' Wikis and Skills are read only on a single-operator host with learning on."""
    try:
        from ..core.knobs import learning_enabled, resolve_knob

        if resolve_knob(_HOSTED_TRIAL_KNOB, "0").value.strip() == "1":
            return False
        return learning_enabled()
    except Exception:  # noqa: BLE001 - an unreadable switch means no cross-project reads
        return False


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left == right or left.resolve() == right.resolve()
    except (OSError, RuntimeError):
        return False


def _sibling_roots(global_root: Path, state: Path, workspace: Path) -> list[KnowledgeRoot]:
    """Wikis and Skills of the newest earlier projects that kept any.

    A project is read where the host keeps it: its state directory under
    ``projects/`` and the workspace its session names, with its Wikis and
    Agent Skills. Of the state directory only the Engineer procedures older
    projects kept there are read; a project's SELF and Manager Skills hold its
    operator's preferences and stay in it.
    """
    projects = global_root / "projects"
    if not _contained(projects, global_root) or not projects.is_dir():
        return []
    candidates: list[tuple[float, Path]] = []
    try:
        with os.scandir(projects) as entries:
            for entry in islice(entries, _MAX_PROJECT_ENTRIES):
                if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
                candidate = projects / entry.name
                if _same_path(candidate, state):
                    continue
                try:
                    candidates.append((entry.stat(follow_symlinks=False).st_mtime, candidate))
                except OSError:
                    continue
    except OSError:
        return []
    candidates.sort(key=lambda row: (-row[0], str(row[1])))
    from ..core.session import SESSION_META_FILE, SessionMeta, resolve_session_workdir

    roots: list[KnowledgeRoot] = []
    siblings = 0
    for _modified, candidate in candidates[:_MAX_SIBLING_CANDIDATES]:
        sid = candidate.name
        try:
            meta: SessionMeta | None = SessionMeta.from_dict(
                json.loads((candidate / SESSION_META_FILE).read_text(encoding="utf-8")))
        except (OSError, ValueError, AttributeError):
            meta = None
        try:
            # Another project's state is read, never repaired.
            sibling = resolve_session_workdir(meta, state_dir=candidate)
        except (OSError, RuntimeError, ValueError):
            continue
        if _same_path(sibling, workspace):
            continue
        found = [(f"earlier project Wiki ({sid})", wiki / "pages", sibling, wiki)
                 for wiki in _bounded_wiki_roots(sibling)]
        found += [(f"earlier project Skill ({sid})", path, boundary, None)
                  for path, boundary in ((candidate / "skills" / "engineer", candidate),
                                         (sibling / NATIVE_SKILLS_DIR, sibling))
                  if path.is_dir()]
        roots += [KnowledgeRoot(kind, path, boundary, scope="project", library=library, source=sid)
                  for kind, path, boundary, library in found]
        siblings += bool(found)
        if siblings >= _MAX_SIBLINGS:
            break
    return roots


class MarkdownKnowledgeRecall:
    def __init__(self, index_path: Path, roots: list[KnowledgeRoot], *,
                 max_documents: int = 256, max_file_bytes: int = _MAX_PAGE_BYTES,
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
        # Taken before the read: a write in between shows as a newer version next time.
        version = ":".join(map(str, file_facts(path)))
        raw = _read_current_file(path, budget)
        remaining_bytes -= len(raw)
        if len(raw) > budget or not _contained(path, root):
            return None, remaining_bytes
        content = raw.decode("utf-8")
        if not content.strip():
            return None, remaining_bytes
        document = KnowledgeDocument(
            f"markdown:{path}", path, source.kind, version, content, root=source,
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

    def _synchronize(self, documents: list[KnowledgeDocument]) -> tuple[list[RecallDocument], bool]:
        indexed = [document.indexed() for document in documents]
        try:
            self.index.sync(indexed)
            return indexed, True
        except EmbeddingUnavailable:
            log.warning("knowledge embedding unavailable; using current lexical recall")
            return indexed, False
        except Exception:
            log.warning("knowledge index sync failed; rebuilding current Markdown", exc_info=True)
        try:
            self.index.rebuild(indexed)
            return indexed, True
        except Exception:
            log.warning("knowledge index unavailable; recalling current Markdown only", exc_info=True)
            return indexed, False

    def sync(self) -> dict[str, int]:
        documents = self._snapshot()
        _, available = self._synchronize(documents)
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
        root = document.root
        if root is not None and root.source and root.library is not None:
            # An earlier project's page names its sources relative to its own workspace.
            details.append(f"workspace `{root.boundary}`")
        if meta.created:
            details.append(meta.created)
        if meta.corrected:
            # A reader sees that the page was corrected before deciding to open it.
            details.append(f"corrected {meta.corrected}")
        summary = meta.description or meta.title
        return (
            f"- {document.kind}: `{document.path}` — {summary} ({'; '.join(details)})"
            + (f" code: {', '.join(f'`{name}`' for name in meta.code)}" if meta.code else "")
            + (f" [{channel}]" if channel else "")
        )

    def _recall(self, objective: str, *, max_entries: int, max_chars: int) -> RecallResult:
        if max_entries <= 0 or max_chars <= 0:
            return RecallResult("")
        documents = self._snapshot()
        indexed, available = self._synchronize(documents)
        if not documents or not objective.strip():
            return RecallResult("")
        scores = lexical_scores(indexed, objective)
        if available:
            try:
                indexed_scores = self.index.scores(objective, documents=indexed)
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
            semantic = not is_lexical(self.index.embedder)
            return (-score.direct, -score.transfer, document.meta.page_kind != "lesson",
                    -score.vector if semantic else 0, str(document.path))

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
        if candidates and not is_lexical(self.index.embedder):
            semantic = max(candidates, key=lambda document: scores[document.id].vector)
            if scores[semantic.id].vector >= _SEMANTIC_MIN_SIMILARITY and semantic not in hits:
                hits = hits[:max(0, limit - 1)] + [semantic]
        if not hits and principles is None:
            return RecallResult("")
        lines = [_RECALL_HEADER, _RECALL_INTRO + (
            _CODE_NOTE if any(document.meta.code for document in hits) else "")]
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


def _operator_roots(global_root: Path) -> list[KnowledgeRoot]:
    """The operator's private profile and notes; private scope, this home only."""
    from ..core.paths import operator_memory_root

    try:
        root = operator_memory_root(global_root).absolute()
    except (OSError, ValueError):
        return []
    return [
        KnowledgeRoot("about the operator", root / "profile.md", root, scope="private", library=root),
        KnowledgeRoot("notes about the operator", root / "pages", root, scope="private", library=root),
    ]


def _shared_wiki_roots(global_root: Path, vertical: str) -> list[KnowledgeRoot]:
    """Every vertical's shared Wiki pages, the project's own first with its
    principles, and the host-wide shared Wiki."""
    from ..core.paths import global_wiki_root, shared_vertical_wiki_root, shared_wiki_root
    from ..wiki.promote import SHARED_VERTICALS_DIRNAME

    roots: list[KnowledgeRoot] = []
    try:
        boundary = shared_wiki_root(global_root).absolute()
        verticals = boundary / SHARED_VERTICALS_DIRNAME
        names = sorted(path.name for path in verticals.iterdir()
                       if not path.name.startswith(".") and path.is_dir() and not path.is_symlink()
                       ) if verticals.is_dir() else []
        for name in dict.fromkeys(filter(None, (vertical, *names))):
            vertical_root = shared_vertical_wiki_root(name, global_root).absolute()
            roots.append(KnowledgeRoot(
                f"{name} knowledge", vertical_root / "pages", boundary,
                scope="vertical", vertical=name, library=vertical_root,
            ))
            if name == vertical:
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


def knowledge_roots(state: Path, global_root: Path | None, workspace: Path | None,
                    skill_store: Any = None) -> list[KnowledgeRoot]:
    """Where recall reads, in the order the bounded snapshot takes them."""
    state = Path(state).absolute()
    roots: list[KnowledgeRoot] = [KnowledgeRoot("project Skill", state / "skills", state)]
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
            roots.append(KnowledgeRoot("native project Skill", workspace / NATIVE_SKILLS_DIR, workspace))
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
        # What Argus knows about the operator comes first of all: the work
        # should fit the person before it fits the field.
        roots[0:0] = _operator_roots(global_root)
        if workspace is not None and _sibling_libraries_enabled():
            roots.extend(_sibling_roots(global_root, state, workspace))
    return roots


def _cited_source_dirs(root: KnowledgeRoot) -> set[Path]:
    """Top-level directories of an earlier project's workspace that its Wiki pages link into.

    Those pages cite the project's own sources (``repos/<name>/...``); a role
    shown them reads those directories, never the rest of that workspace.
    """
    from ..wiki.promote import linked_files

    workspace, library = root.boundary.absolute(), (root.library or root.path).absolute()
    found: set[Path] = set()
    for page in islice(root.path.rglob("*.md"), _MAX_CITING_PAGES):
        try:
            text = _read_current_file(page, _MAX_PAGE_BYTES).decode("utf-8", "replace")
        except (OSError, ValueError):
            continue
        for target in linked_files(page, text, workspace):
            if target.is_relative_to(library):
                continue
            top = workspace / target.relative_to(workspace).parts[0]
            if not top.name.startswith(".") and top.is_dir() and _contained(top, workspace):
                found.add(top)
    return found


def knowledge_read_dirs(state: Path | None, global_root: Path | None, workspace: Path | None) -> list[str]:
    """The directories recall can point a role at outside its workspace.

    A sandboxed backend reads only its workspace and the directories it is
    given, so a role that is shown recall's pointers is given these. The
    operator's private memory is recalled but never handed to a backend.
    """
    if state is None:
        return []
    inside = Path(workspace).absolute() if workspace is not None else None
    roots = [root for root in knowledge_roots(state, global_root, workspace) if root.scope != "private"]
    libraries = {(root.library or root.path).absolute() for root in roots}
    for root in roots:
        if root.source and root.library is not None:
            libraries |= _cited_source_dirs(root)
    found: list[Path] = []
    for library in sorted(libraries, key=lambda path: (len(path.parts), str(path))):
        if (library.is_dir() and not any(library.is_relative_to(path) for path in found)
                and not (inside is not None and library.is_relative_to(inside))):
            found.append(library)
    return [str(path) for path in found]


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
    roots = knowledge_roots(state, getattr(memory, "global_root", None), workspace, skill_store)
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
