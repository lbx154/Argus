"""Persistent memory primitives for life-mode.

Three storage shapes:

- ``Journal``: append-only ``journal.jsonl``. Each entry is one mission
  outcome (or a manually-recorded note). Atomic append via ``O_APPEND``
  on POSIX so concurrent writers don't interleave.
- ``Backlog``: ordered ``backlog.jsonl`` of pending mission objectives.
  Status field on each row toggles ``pending`` → ``running`` → ``done``
  / ``failed`` / ``skipped``. We rewrite the whole file on status
  changes; the file is small (tens-to-hundreds of items).
- ``IdentityCard``: a single ``identity.md`` markdown file the user
  edits freely. We just read it.

The :class:`LifeMemory` facade bundles all three plus a small retrieval
helper that returns the most-relevant N journal entries for a new
objective via word-overlap (keyword Jaccard). Recency is a tiebreaker.

This module has **no LLM dependency** so it's testable and importable
in any environment (we use it from the CLI even when the API key is
missing).
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def default_life_dir() -> Path:
    """Return the on-disk root for the user's life directory.

    Honours ``ARGUS_SKILL_LIFE_DIR`` so tests / multi-tenant setups can
    redirect to a tmp location. Defaults to ``~/.argus-skill/life``.
    """
    raw = os.environ.get("ARGUS_SKILL_LIFE_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".argus-skill" / "life"


# ---------------------------------------------------------------------------
# Atomic JSONL helpers
# ---------------------------------------------------------------------------

def _atomic_append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Append a single JSON-serializable dict as one line.

    Uses ``open(..., 'a')`` which on POSIX is atomic for writes <
    ``PIPE_BUF`` (4 KiB on Linux). Mission summaries live well under
    that. We add a trailing ``\\n`` and never embed raw newlines in
    values (json.dumps handles escaping).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, sort_keys=True)
    if "\n" in line:  # paranoia — json.dumps shouldn't emit raw newlines
        line = line.replace("\n", " ")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rows.append(json.loads(raw))
            except json.JSONDecodeError:
                # Tolerate partial trailing lines from a crash; skip.
                continue
    return rows


def _atomic_rewrite_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Replace ``path`` atomically with the given rows.

    We write to a sibling ``.tmp`` then ``os.replace``. Survives crashes
    in the middle of a status update.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

@dataclass
class JournalEntry:
    """One row in ``journal.jsonl``.

    ``kind`` is a short tag: ``mission_complete``, ``mission_failed``,
    ``user_note``, ``budget_pause``, etc.

    ``summary`` is the human-readable one-paragraph "what happened".

    ``tags`` are free-form strings used by retrieval (typically: skill
    name, repo path, key topic words).
    """

    id: str
    ts: float
    kind: str
    title: str
    summary: str
    tags: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        kind: str,
        title: str,
        summary: str,
        tags: list[str] | None = None,
        cost_usd: float = 0.0,
        extra: dict[str, Any] | None = None,
    ) -> "JournalEntry":
        return cls(
            id=uuid.uuid4().hex[:12],
            ts=time.time(),
            kind=kind,
            title=title.strip(),
            summary=summary.strip(),
            tags=list(tags or []),
            cost_usd=float(cost_usd),
            extra=dict(extra or {}),
        )

    def to_jsonable(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_jsonable(cls, row: dict[str, Any]) -> "JournalEntry":
        return cls(
            id=str(row.get("id", uuid.uuid4().hex[:12])),
            ts=float(row.get("ts", time.time())),
            kind=str(row.get("kind", "unknown")),
            title=str(row.get("title", "")),
            summary=str(row.get("summary", "")),
            tags=list(row.get("tags", [])),
            cost_usd=float(row.get("cost_usd", 0.0)),
            extra=dict(row.get("extra", {})),
        )


class Journal:
    """Append-only persistent journal."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # --- write ---
    def append(self, entry: JournalEntry) -> None:
        _atomic_append_jsonl(self.path, entry.to_jsonable())

    # --- read ---
    def all(self) -> list[JournalEntry]:
        return [JournalEntry.from_jsonable(r) for r in _read_jsonl(self.path)]

    def tail(self, n: int = 20) -> list[JournalEntry]:
        rows = _read_jsonl(self.path)
        return [JournalEntry.from_jsonable(r) for r in rows[-n:]]

    def total_cost_since(self, ts: float) -> float:
        return sum(
            float(r.get("cost_usd", 0.0))
            for r in _read_jsonl(self.path)
            if float(r.get("ts", 0.0)) >= ts
        )


# ---------------------------------------------------------------------------
# Backlog
# ---------------------------------------------------------------------------

_BACKLOG_STATUSES = {"pending", "running", "done", "failed", "skipped"}


@dataclass
class BacklogItem:
    id: str
    ts: float
    title: str
    objective: str  # full instruction handed to the engineer
    status: str = "pending"
    priority: int = 100  # smaller = higher priority
    max_cost_usd: float = 1.0
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    started_ts: float | None = None
    finished_ts: float | None = None
    last_error: str = ""

    @classmethod
    def new(
        cls,
        *,
        title: str,
        objective: str,
        priority: int = 100,
        max_cost_usd: float = 1.0,
        tags: list[str] | None = None,
        notes: str = "",
    ) -> "BacklogItem":
        return cls(
            id=uuid.uuid4().hex[:12],
            ts=time.time(),
            title=title.strip(),
            objective=objective.strip(),
            priority=int(priority),
            max_cost_usd=float(max_cost_usd),
            tags=list(tags or []),
            notes=notes.strip(),
        )

    def to_jsonable(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_jsonable(cls, row: dict[str, Any]) -> "BacklogItem":
        status = str(row.get("status", "pending"))
        if status not in _BACKLOG_STATUSES:
            status = "pending"
        return cls(
            id=str(row.get("id", uuid.uuid4().hex[:12])),
            ts=float(row.get("ts", time.time())),
            title=str(row.get("title", "")),
            objective=str(row.get("objective", "")),
            status=status,
            priority=int(row.get("priority", 100)),
            max_cost_usd=float(row.get("max_cost_usd", 1.0)),
            tags=list(row.get("tags", [])),
            notes=str(row.get("notes", "")),
            started_ts=row.get("started_ts"),
            finished_ts=row.get("finished_ts"),
            last_error=str(row.get("last_error", "")),
        )


class Backlog:
    """Ordered persistent backlog of missions.

    Pending items are sorted by ``(priority asc, ts asc)`` so callers
    can always ``next_pending()`` to get the head.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # --- io ---
    def _load(self) -> list[BacklogItem]:
        return [BacklogItem.from_jsonable(r) for r in _read_jsonl(self.path)]

    def _save(self, items: Iterable[BacklogItem]) -> None:
        _atomic_rewrite_jsonl(self.path, (it.to_jsonable() for it in items))

    # --- write ---
    def add(self, item: BacklogItem) -> BacklogItem:
        items = self._load()
        items.append(item)
        self._save(items)
        return item

    def update(self, item_id: str, **fields: Any) -> BacklogItem | None:
        items = self._load()
        out: BacklogItem | None = None
        for it in items:
            if it.id == item_id:
                for k, v in fields.items():
                    if hasattr(it, k):
                        setattr(it, k, v)
                if "status" in fields and fields["status"] not in _BACKLOG_STATUSES:
                    it.status = "pending"
                out = it
                break
        if out is not None:
            self._save(items)
        return out

    def mark_running(self, item_id: str) -> BacklogItem | None:
        return self.update(item_id, status="running", started_ts=time.time())

    def mark_done(self, item_id: str) -> BacklogItem | None:
        return self.update(item_id, status="done", finished_ts=time.time())

    def mark_failed(self, item_id: str, *, error: str = "") -> BacklogItem | None:
        return self.update(
            item_id, status="failed", finished_ts=time.time(), last_error=error
        )

    def remove(self, item_id: str) -> bool:
        items = self._load()
        new = [it for it in items if it.id != item_id]
        if len(new) == len(items):
            return False
        self._save(new)
        return True

    # --- read ---
    def all(self) -> list[BacklogItem]:
        return self._load()

    def pending(self) -> list[BacklogItem]:
        items = [it for it in self._load() if it.status == "pending"]
        items.sort(key=lambda it: (it.priority, it.ts))
        return items

    def next_pending(self) -> BacklogItem | None:
        pend = self.pending()
        return pend[0] if pend else None


# ---------------------------------------------------------------------------
# Identity card
# ---------------------------------------------------------------------------

_DEFAULT_IDENTITY = """\
# argus-skill — life identity

You are a persistent assistant.

## Voice
- Concise, technical, frank.
- Surface uncertainty rather than bluff.

## Red lines
- Never delete user data without explicit confirmation.
- Never push to a remote unless an objective explicitly says so.
- Pause and append a journal entry if budget caps are reached.
"""


class IdentityCard:
    """A single markdown file the user can hand-edit.

    We never overwrite an existing card. ``ensure_default()`` only
    seeds it once on first ``argus-skill life init``.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8")

    def ensure_default(self) -> bool:
        if self.path.exists():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(_DEFAULT_IDENTITY, encoding="utf-8")
        return True


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")

# Tiny English stopword list — keeps Jaccard from matching on filler.
# We do NOT pull in NLTK; this is a few dozen words, deliberately small.
_STOPWORDS = frozenset(
    """a an the and or but if then else of to for from in on at by with as is are was
    were be been being have has had do does did this that these those it its his her
    him she they them their there here can could should would may might will shall not
    no yes than too very also just only own same so such other any some all into out
    over under up down off about above below between through during before after when
    while where why how what which who whom add use using used uses make made get got
    set put run ran new old via etc""".split()
)


def _tokens(s: str) -> set[str]:
    return {
        t.lower()
        for t in _TOKEN_RE.findall(s)
        if len(t) >= 3 and t.lower() not in _STOPWORDS
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


# ---------------------------------------------------------------------------
# LifeMemory facade
# ---------------------------------------------------------------------------

@dataclass
class LifeMemory:
    """Facade bundling identity / journal / backlog plus retrieval."""

    root: Path
    identity: IdentityCard
    journal: Journal
    backlog: Backlog

    @classmethod
    def open(cls, root: Path | None = None) -> "LifeMemory":
        root = Path(root) if root is not None else default_life_dir()
        root.mkdir(parents=True, exist_ok=True)
        return cls(
            root=root,
            identity=IdentityCard(root / "identity.md"),
            journal=Journal(root / "journal.jsonl"),
            backlog=Backlog(root / "backlog.jsonl"),
        )

    def init(self) -> dict[str, bool]:
        """Idempotently seed the directory; returns what was created."""
        self.root.mkdir(parents=True, exist_ok=True)
        return {
            "identity": self.identity.ensure_default(),
            "journal": self._touch(self.journal.path),
            "backlog": self._touch(self.backlog.path),
        }

    @staticmethod
    def _touch(p: Path) -> bool:
        if p.exists():
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        return True

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def relevant_journal_for(
        self,
        objective: str,
        *,
        max_entries: int = 3,
        min_score: float = 0.05,
        recency_n: int = 30,
    ) -> list[JournalEntry]:
        """Return up to ``max_entries`` journal entries most relevant to
        ``objective``, scored by token-Jaccard against title+summary+tags.

        Only the most-recent ``recency_n`` entries are considered, both
        for cost and because old context is usually less relevant. If no
        entry meets ``min_score`` we return an empty list rather than
        injecting noise.
        """
        recent = self.journal.tail(recency_n)
        if not recent:
            return []
        obj_tokens = _tokens(objective)
        if not obj_tokens:
            return []
        scored: list[tuple[float, float, JournalEntry]] = []
        for entry in recent:
            entry_tokens = _tokens(
                " ".join([entry.title, entry.summary, " ".join(entry.tags)])
            )
            score = _jaccard(obj_tokens, entry_tokens)
            if score >= min_score:
                scored.append((score, entry.ts, entry))
        # sort: higher score first, recency tiebreaker
        scored.sort(key=lambda t: (-t[0], -t[1]))
        return [e for _, _, e in scored[:max_entries]]

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def render_prelude(
        self,
        *,
        objective: str,
        identity_chars: int = 600,
        max_journal_entries: int = 3,
    ) -> str:
        """Render the memory block we inject as ``prelude_context``.

        The header explicitly marks the block as non-authoritative so
        the engineer/reviewer prompts can downweight it on conflict.
        Returns an empty string if there's nothing useful to inject.
        """
        identity = self.identity.read().strip()
        if identity_chars > 0:
            identity = identity[:identity_chars]
        relevant = self.relevant_journal_for(
            objective, max_entries=max_journal_entries
        )

        if not identity and not relevant:
            return ""

        lines: list[str] = []
        lines.append("### Memory context (non-authoritative)")
        lines.append(
            "The following identity card and prior-mission notes are advisory. "
            "If they conflict with the current objective, the live repo state, "
            "or explicit user instructions, **ignore them**."
        )
        if identity:
            lines.append("")
            lines.append("#### Identity")
            lines.append(identity.strip())
        if relevant:
            lines.append("")
            lines.append("#### Possibly-relevant prior missions")
            for entry in relevant:
                # one-paragraph compact form
                ts_iso = time.strftime("%Y-%m-%d", time.localtime(entry.ts))
                lines.append(
                    f"- **{ts_iso} · {entry.title}** ({entry.kind}): "
                    f"{entry.summary}"
                )
        return "\n".join(lines).strip() + "\n"
