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
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

fcntl: Any
try:  # pragma: no cover - platform-specific import
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

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


def _read_jsonl_tail(path: Path, n: int) -> list[dict[str, Any]]:
    """Return the last ``n`` JSONL rows without scanning the whole file."""
    if n <= 0 or not path.exists():
        return []

    rows_rev: list[dict[str, Any]] = []
    chunk_size = 32 * 1024
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            file_pos = fh.tell()
            buffer = b""
            while file_pos > 0 and len(rows_rev) < n:
                read_size = min(chunk_size, file_pos)
                file_pos -= read_size
                fh.seek(file_pos, os.SEEK_SET)
                buffer = fh.read(read_size) + buffer
                parts = buffer.split(b"\n")
                buffer = parts[0]
                for raw in reversed(parts[1:]):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rows_rev.append(json.loads(raw.decode("utf-8")))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if len(rows_rev) >= n:
                        break
            if len(rows_rev) < n:
                raw = buffer.strip()
                if raw:
                    try:
                        rows_rev.append(json.loads(raw.decode("utf-8")))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        pass
    except OSError:
        return []

    rows_rev.reverse()
    return rows_rev


def _journal_rollover_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".1")


def _read_jsonl_history(path: Path) -> list[dict[str, Any]]:
    """Return the journal plus its most recent rollover, in order."""
    rows: list[dict[str, Any]] = []
    backup = _journal_rollover_path(path)
    if backup.exists():
        rows.extend(_read_jsonl(backup))
    rows.extend(_read_jsonl(path))
    return rows


def _read_jsonl_tail_history(path: Path, n: int) -> list[dict[str, Any]]:
    """Return the tail of the live journal plus its most recent rollover."""
    if n <= 0:
        return []
    rows = _read_jsonl_tail(path, n)
    if len(rows) >= n:
        return rows
    backup = _journal_rollover_path(path)
    if not backup.exists():
        return rows
    needed = n - len(rows)
    return _read_jsonl_tail(backup, needed) + rows


def _path_signature(path: Path) -> tuple[int, int, int, int] | None:
    """Return a cheap fingerprint for the current on-disk file state."""
    try:
        stat = path.stat()
    except OSError:
        return None
    ino = int(getattr(stat, "st_ino", 0) or 0)
    dev = int(getattr(stat, "st_dev", 0) or 0)
    return (int(stat.st_mtime_ns), int(stat.st_size), dev, ino)


def _atomic_rewrite_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Replace ``path`` atomically with the given rows.

    We write to a unique sibling temp file then ``os.replace``. Survives
    crashes in the middle of a status update and avoids filename
    collisions when multiple processes rewrite the same backlog.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            tmp_path = Path(fh.name)
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


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

    # When the journal file grows past this many bytes, ``append``
    # rotates it to ``<path>.1`` (single previous generation, simple).
    # Sized to comfortably hold ~30 days of an active 7×24 daemon
    # without losing recent context to truncation.
    ROTATE_BYTES = 50 * 1024 * 1024  # 50 MiB

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._total_cost_cache: dict[float, tuple[tuple, float]] = {}

    # --- write ---
    def append(self, entry: JournalEntry) -> None:
        self._maybe_rotate()
        _atomic_append_jsonl(self.path, entry.to_jsonable())

    def _maybe_rotate(self) -> None:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return
        except OSError:
            return
        if size < self.ROTATE_BYTES:
            return
        backup = self.path.with_suffix(self.path.suffix + ".1")
        try:
            if backup.exists():
                backup.unlink()
            self.path.rename(backup)
        except OSError:
            # Rotation is best-effort: a busy / read-only filesystem
            # must not crash the daemon. Worst case the journal keeps
            # growing until the next attempt.
            pass

    # --- read ---
    def all(self) -> list[JournalEntry]:
        return [JournalEntry.from_jsonable(r) for r in _read_jsonl_history(self.path)]

    def tail(self, n: int = 20) -> list[JournalEntry]:
        if n <= 0:
            return []
        rows = _read_jsonl_tail_history(self.path, n)
        return [JournalEntry.from_jsonable(r) for r in rows]

    def total_cost_since(self, ts: float) -> float:
        signature = (
            _path_signature(self.path),
            _path_signature(_journal_rollover_path(self.path)),
        )
        cached = self._total_cost_cache.get(ts)
        if cached is not None and cached[0] == signature:
            return cached[1]
        total = sum(
            float(r.get("cost_usd", 0.0))
            for r in _read_jsonl_history(self.path)
            if float(r.get("ts", 0.0)) >= ts
        )
        self._total_cost_cache[ts] = (signature, total)
        return total


# ---------------------------------------------------------------------------
# Backlog
# ---------------------------------------------------------------------------

_BACKLOG_STATUSES = {"pending", "running", "done", "failed", "skipped"}
_TERMINAL_STATUSES = {"done", "failed", "skipped"}


class IllegalStateTransition(RuntimeError):
    """Raised when a status update would resurrect a terminal item.

    Defensive against the entire class of bugs where a code path
    accidentally re-runs an already-completed mission. ``done``,
    ``failed``, and ``skipped`` are sinks: the only way to get a
    new attempt at the same work is to enqueue a fresh
    :class:`BacklogItem` (so it gets a new id and audit trail).
    """


@dataclass
class BacklogItem:
    id: str
    ts: float
    title: str
    objective: str  # full instruction handed to the engineer
    status: str = "pending"
    priority: int = 100  # smaller = higher priority
    max_cost_usd: float = 30.0
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    started_ts: float | None = None
    finished_ts: float | None = None
    last_error: str = ""
    # --- iteration loop fields (Phase-7) -------------------------------
    # When ``iterate`` is True the supervisor, after a successful
    # ``done`` verdict, hands the produced artefacts to a Critic agent.
    # If the Critic finds concrete improvements the item is requeued
    # for another mission cycle until either the cost budget or the
    # cycle ceiling is hit. ``original_objective`` preserves the
    # operator's first-cycle instruction so subsequent cycles can be
    # framed as "polish what you already built".
    iterate: bool = True
    iteration_max_cycles: int = 6
    iteration_budget_usd: float = 30.0
    iteration_cycles_done: int = 0
    iteration_cost_usd: float = 0.0
    original_objective: str = ""
    orphan_retries: int = 0

    @classmethod
    def new(
        cls,
        *,
        title: str,
        objective: str,
        priority: int = 100,
        max_cost_usd: float = 30.0,
        tags: list[str] | None = None,
        notes: str = "",
        iterate: bool = True,
        iteration_max_cycles: int = 6,
        iteration_budget_usd: float = 30.0,
    ) -> "BacklogItem":
        objective = objective.strip()
        return cls(
            id=uuid.uuid4().hex[:12],
            ts=time.time(),
            title=title.strip(),
            objective=objective,
            priority=int(priority),
            max_cost_usd=float(max_cost_usd),
            tags=list(tags or []),
            notes=notes.strip(),
            iterate=bool(iterate),
            iteration_max_cycles=int(iteration_max_cycles),
            iteration_budget_usd=float(iteration_budget_usd),
            original_objective=objective,
        )

    def to_jsonable(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_jsonable(cls, row: dict[str, Any]) -> "BacklogItem":
        status = str(row.get("status", "pending"))
        if status not in _BACKLOG_STATUSES:
            status = "pending"
        objective = str(row.get("objective", ""))
        return cls(
            id=str(row.get("id", uuid.uuid4().hex[:12])),
            ts=float(row.get("ts", time.time())),
            title=str(row.get("title", "")),
            objective=objective,
            status=status,
            priority=int(row.get("priority", 100)),
            max_cost_usd=float(row.get("max_cost_usd", 30.0)),
            tags=list(row.get("tags", [])),
            notes=str(row.get("notes", "")),
            started_ts=row.get("started_ts"),
            finished_ts=row.get("finished_ts"),
            last_error=str(row.get("last_error", "")),
            iterate=bool(row.get("iterate", False)),
            iteration_max_cycles=int(row.get("iteration_max_cycles", 6)),
            iteration_budget_usd=float(row.get("iteration_budget_usd", 30.0)),
            iteration_cycles_done=int(row.get("iteration_cycles_done", 0)),
            iteration_cost_usd=float(row.get("iteration_cost_usd", 0.0)),
            original_objective=str(row.get("original_objective", objective)),
            orphan_retries=int(row.get("orphan_retries", 0)),
        )


class Backlog:
    """Ordered persistent backlog of missions.

    Pending items are sorted by ``(priority asc, ts asc)`` so callers
    can always ``next_pending()`` to get the head.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock_path = self.path.parent / f"{self.path.name}.lock"

    # --- io ---
    def _load(self) -> list[BacklogItem]:
        return [BacklogItem.from_jsonable(r) for r in _read_jsonl(self.path)]

    def _save(self, items: Iterable[BacklogItem]) -> None:
        _atomic_rewrite_jsonl(self.path, (it.to_jsonable() for it in items))

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialize backlog read-modify-write operations across processes."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as fh:
            if fcntl is not None:  # POSIX
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            else:  # pragma: no cover - Windows fallback
                import msvcrt

                lock = getattr(msvcrt, "locking")
                lk_lock = getattr(msvcrt, "LK_LOCK")
                lk_unlock = getattr(msvcrt, "LK_UNLCK")
                fh.seek(0)
                lock(fh.fileno(), lk_lock, 1)
                try:
                    yield
                finally:
                    fh.seek(0)
                    lock(fh.fileno(), lk_unlock, 1)

    # --- write ---
    def add(self, item: BacklogItem) -> BacklogItem:
        with self._locked():
            items = self._load()
            items.append(item)
            self._save(items)
        return item

    def update(self, item_id: str, **fields: Any) -> BacklogItem | None:
        with self._locked():
            items = self._load()
            out: BacklogItem | None = None
            for it in items:
                if it.id == item_id:
                    if "status" in fields:
                        new_status = fields["status"]
                        if new_status not in _BACKLOG_STATUSES:
                            new_status = "pending"
                            fields["status"] = "pending"
                        if (
                            it.status in _TERMINAL_STATUSES
                            and new_status not in _TERMINAL_STATUSES
                        ):
                            raise IllegalStateTransition(
                                f"backlog item {item_id} is in terminal state "
                                f"{it.status!r}; refusing transition to "
                                f"{new_status!r}. Enqueue a new item instead."
                            )
                    for k, v in fields.items():
                        if hasattr(it, k):
                            setattr(it, k, v)
                    out = it
                    break
            if out is not None:
                self._save(items)
            return out

    def claim_next(self) -> BacklogItem | None:
        """Atomically pick the head pending item and flip it to ``running``.

        Replaces the ``next_pending()`` + ``mark_running()`` pair so the
        TOCTOU window between "see a pending row" and "claim it" closes.
        Returns the claimed item (with its in-memory ``status`` already
        ``running`` and ``started_ts`` set), or ``None`` if nothing is
        pending. We rewrite the file under the same lock that ``_save``
        already uses, so two concurrent callers cannot both win.
        """
        with self._locked():
            items = self._load()
            pending = [it for it in items if it.status == "pending"]
            if not pending:
                return None
            pending.sort(key=lambda it: (it.priority, it.ts))
            head = pending[0]
            head.status = "running"
            head.started_ts = time.time()
            self._save(items)
            return head

    def reap_orphans(
        self,
        *,
        max_retries: int = 3,
        error: str = "orphaned: previous process did not finish",
    ) -> list[BacklogItem]:
        """Recover items left ``running`` by a crashed process.

        Items with fewer than *max_retries* orphan recoveries are reset
        to ``pending`` so the next supervisor pass retries them. Items
        that have already been orphaned *max_retries* times are marked
        ``failed`` to prevent poison-pill loops.

        Returns the list of affected items (both re-queued and failed).
        """
        with self._locked():
            items = self._load()
            reaped: list[BacklogItem] = []
            for it in items:
                if it.status == "running":
                    it.orphan_retries += 1
                    if it.orphan_retries > max_retries:
                        it.status = "failed"
                        it.finished_ts = time.time()
                        if not it.last_error:
                            it.last_error = f"{error} (exceeded {max_retries} retries)"
                    else:
                        it.status = "pending"
                        it.started_ts = None
                        it.last_error = error
                    reaped.append(it)
            if reaped:
                self._save(items)
            return reaped

    def mark_running(self, item_id: str) -> BacklogItem | None:
        return self.update(item_id, status="running", started_ts=time.time())

    def mark_done(self, item_id: str) -> BacklogItem | None:
        return self.update(item_id, status="done", finished_ts=time.time())

    def requeue_for_iteration(
        self,
        item_id: str,
        *,
        new_objective: str,
        cost_delta_usd: float,
    ) -> BacklogItem | None:
        """Move a ``running`` item back to ``pending`` for another cycle.

        Bypasses the terminal-state guard in :meth:`update` because the
        item never reached a terminal state — the iteration loop intercepts
        the would-be ``done`` and re-arms the same item with a polished
        objective. Increments ``iteration_cycles_done`` and accumulates
        ``iteration_cost_usd``.
        """
        with self._locked():
            items = self._load()
            out: BacklogItem | None = None
            for it in items:
                if it.id == item_id:
                    if it.status not in {"running", "pending"}:
                        return None
                    it.status = "pending"
                    it.objective = new_objective.strip() or it.objective
                    it.iteration_cycles_done += 1
                    it.iteration_cost_usd = round(
                        it.iteration_cost_usd + max(0.0, float(cost_delta_usd)), 6
                    )
                    it.started_ts = None
                    it.finished_ts = None
                    it.last_error = ""
                    out = it
                    break
            if out is not None:
                self._save(items)
            return out

    def stop_iteration(
        self, item_id: str, *, reason: str = "stopped by operator"
    ) -> BacklogItem | None:
        """Disable iteration on an item (operator-level kill switch).

        If the item is currently iterating-pending we mark it ``done``
        with a note. If it is ``running`` we leave it alone; the
        supervisor will check ``iterate`` after the current cycle and
        finalize naturally.
        """
        with self._locked():
            items = self._load()
            out: BacklogItem | None = None
            for it in items:
                if it.id == item_id:
                    it.iterate = False
                    if it.status == "pending":
                        it.status = "done"
                        it.finished_ts = time.time()
                        if not it.notes:
                            it.notes = reason
                    out = it
                    break
            if out is not None:
                self._save(items)
            return out

    def mark_failed(self, item_id: str, *, error: str = "") -> BacklogItem | None:
        return self.update(
            item_id, status="failed", finished_ts=time.time(), last_error=error
        )

    def remove(self, item_id: str) -> bool:
        with self._locked():
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
# argus-skill — operator identity card

This file is your **persistent, hand-editable** identity. The supervisor
reads it before every mission and treats every section below as
operator-binding. Edit freely.

## Persona
- **Name / call-sign**: (e.g. "argus-helper for Alex") <!-- fill in -->
- **Operator name**: <!-- fill in -->
- **Role / focus**: senior coding agent for one operator's projects.
- **Voice**: concise, technical, frank. Surface uncertainty rather than
  bluff. No filler ("Sure!", "Of course"); start with the answer.

## Working hours (operator local time)
- Active hours: 24/7 (override if you want quiet hours, e.g. `22:00–08:00`).
- During quiet hours: keep running but defer notifications until next
  active window.

## Escalation
- Notify channel: <!-- e.g. webhook URL, email, telegram chat_id -->
- Escalate immediately on: `mission_failed`, `auth_failure`,
  `budget_pause`, `mission_orphaned`. Otherwise summarize at end of day.

## Tooling preferences
- Backend: codex (default). Memory backend is test-only.
- Workdir convention: `~/argus-skill-tasks/<slug>/` per mission unless
  the operator pins a specific path.
- Run pytest with `-q`. Run `ruff check` before declaring done.

## Red lines (NEVER cross)
- Never delete operator data without explicit confirmation in the same
  session (a backlog item description does NOT count as confirmation).
- Never push to a remote, force-push, or rewrite git history unless the
  objective explicitly says so. `git rebase --root` and
  `git push --force` require operator typed approval.
- Never share secrets, tokens, or `.env` contents in any user-visible
  output.
- Never replace working operator code with a stub or placeholder. If a
  refactor must remove a feature temporarily, stop and ask first.
- Pause and append a journal entry of kind `budget_pause` when budget
  caps are reached; do not silently retry.

## Always-do
- Read this card and the per-project card (if any) before each mission.
- End every engineer round with a verbatim `## Verification` block
  showing actual command output (pytest, ruff, mypy, etc.).
- When the reviewer rejects, address its concrete `next_action`; do not
  ignore prior reviewer guidance.
- When in doubt: prefer `continue` over `blocked`; ask the operator
  through the inbox bus only when a missing credential or hardware
  truly blocks all progress.

## Operator notes
<!-- Free-form: anything you want the agent to remember about you,
your habits, your projects, conventions. The agent reads this every
mission. -->
"""


_DEFAULT_PROJECT_CARD = """\
# {label}

(Edit me — this is the per-project identity card. Capture conventions,
folder layout, "always do X / never touch Y" rules, contact points for
the team, etc. The agent reads this before every mission targeting
this project.)

## Conventions

## Red lines
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


class ProjectCard:
    """Per-project counterpart to :class:`IdentityCard`.

    Same on-disk shape (a free-form markdown file) but seeded with a
    project-scoped template that reminds the user to capture
    conventions / red lines for *this* repo.
    """

    def __init__(self, path: Path, *, label: str = "this project") -> None:
        self.path = Path(path)
        self.label = label

    def read(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8")

    def ensure_default(self) -> bool:
        if self.path.exists():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rendered = _DEFAULT_PROJECT_CARD.format(label=self.label or "this project")
        self.path.write_text(rendered, encoding="utf-8")
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


# ---------------------------------------------------------------------------
# GlobalMemory + ProjectMemory (Phase 2 split)
# ---------------------------------------------------------------------------
#
# Phase 2 of the unification refactor splits the single-rooted
# :class:`LifeMemory` into two narrower facades:
#
# * :class:`GlobalMemory` — agent-wide identity card and cross-project
#   journal under ``~/.argus-skill/``.
# * :class:`ProjectMemory` — per-project card, memory log, and backlog
#   under ``~/.argus-skill/projects/<fingerprint>/``. Lazy-created on
#   first access so we don't litter ``projects/`` with empty trees.
#
# :class:`MemoryBundle` is a thin convenience wrapper that holds one of
# each plus a unified :meth:`render_prelude` that merges global identity,
# project card, and the most-relevant entries from both journals.
#
# :class:`LifeMemory` is unchanged; existing code keeps working.

def _resolve_global_root() -> Path:
    """Return the global agent root, going through ``core.paths``.

    The local import is deliberate: ``argus_skill.core.paths`` may
    transitively import from ``argus_skill.life`` in future phases, and
    a top-level import here would risk a circular reference.
    """
    from ..core import paths as core_paths

    return core_paths.global_root()


def _resolve_project_root(
    fingerprint: str, *, global_root: Path | None = None
) -> Path:
    if global_root is not None:
        return Path(global_root) / "projects" / fingerprint
    from ..core import paths as core_paths

    return core_paths.project_root(fingerprint)


@dataclass
class GlobalMemory:
    """Agent-wide identity + journal under ``~/.argus-skill/``.

    The directory is *lazy*: nothing is written until you call
    :meth:`init` (idempotent) or perform a write through one of the
    sub-objects (which create their parent dirs on demand).
    """

    root: Path
    identity: IdentityCard
    journal: Journal

    @classmethod
    def open(cls, root: Path | None = None) -> "GlobalMemory":
        actual = Path(root) if root is not None else _resolve_global_root()
        return cls(
            root=actual,
            identity=IdentityCard(actual / "identity.md"),
            journal=Journal(actual / "journal.jsonl"),
        )

    def init(self) -> dict[str, bool]:
        """Idempotently seed the global directory; returns what was created."""
        self.root.mkdir(parents=True, exist_ok=True)
        return {
            "identity": self.identity.ensure_default(),
            "journal": _touch_file(self.journal.path),
        }

    def relevant_journal_for(
        self,
        objective: str,
        *,
        max_entries: int = 3,
        min_score: float = 0.05,
        recency_n: int = 30,
    ) -> list[JournalEntry]:
        return _score_journal(
            self.journal,
            objective,
            max_entries=max_entries,
            min_score=min_score,
            recency_n=recency_n,
        )


@dataclass
class ProjectMemory:
    """Per-project memory under ``~/.argus-skill/projects/<fingerprint>/``.

    Holds three things:

    * ``project_card`` — markdown card describing the repo (conventions,
      red lines, contact points). Seeded on first ``init()``.
    * ``memory`` — append-only journal scoped to this project. Used the
      same way as :class:`Journal` but kept separate from the global log
      so cross-project search doesn't leak unrelated context.
    * ``backlog`` — pending mission queue scoped to this project.
    """

    fingerprint: str
    label: str
    root: Path
    project_card: ProjectCard
    memory: Journal
    backlog: Backlog

    @classmethod
    def open(
        cls,
        fingerprint: str,
        *,
        label: str | None = None,
        global_root: Path | None = None,
    ) -> "ProjectMemory":
        if not fingerprint:
            raise ValueError("ProjectMemory.open requires a non-empty fingerprint")
        root = _resolve_project_root(fingerprint, global_root=global_root)
        resolved_label = label or fingerprint
        return cls(
            fingerprint=fingerprint,
            label=resolved_label,
            root=root,
            project_card=ProjectCard(root / "project.md", label=resolved_label),
            memory=Journal(root / "memory.jsonl"),
            backlog=Backlog(root / "backlog.jsonl"),
        )

    def init(self) -> dict[str, bool]:
        """Create the project directory + seed defaults if missing."""
        self.root.mkdir(parents=True, exist_ok=True)
        return {
            "project_card": self.project_card.ensure_default(),
            "memory": _touch_file(self.memory.path),
            "backlog": _touch_file(self.backlog.path),
        }

    def relevant_memory_for(
        self,
        objective: str,
        *,
        max_entries: int = 3,
        min_score: float = 0.05,
        recency_n: int = 30,
    ) -> list[JournalEntry]:
        return _score_journal(
            self.memory,
            objective,
            max_entries=max_entries,
            min_score=min_score,
            recency_n=recency_n,
        )


@dataclass
class MemoryBundle:
    """Bundles one :class:`GlobalMemory` plus one :class:`ProjectMemory`.

    Phase-3 reviewer prompt-builder + Phase-5 unified REPL both want
    "everything the agent knows about this run" in one object instead
    of juggling two facades. Construct via :meth:`for_cwd` to get
    automatic project-fingerprint resolution.
    """

    global_mem: GlobalMemory
    project: ProjectMemory

    @classmethod
    def for_cwd(
        cls,
        cwd: Path | str | None = None,
        *,
        global_root: Path | None = None,
    ) -> "MemoryBundle":
        from ..core.project import project_fingerprint  # local: avoid cycle

        identity = project_fingerprint(cwd)
        return cls(
            global_mem=GlobalMemory.open(global_root),
            project=ProjectMemory.open(
                identity.fingerprint,
                label=identity.label,
                global_root=global_root,
            ),
        )

    def init(self) -> dict[str, dict[str, bool]]:
        return {
            "global": self.global_mem.init(),
            "project": self.project.init(),
        }

    def render_prelude(
        self,
        *,
        objective: str,
        identity_chars: int = 600,
        project_chars: int = 600,
        max_global_entries: int = 2,
        max_project_entries: int = 3,
    ) -> str:
        """Render a unified memory prelude for prompt injection.

        Order is: global identity → project card → relevant project
        memories → relevant global journal entries. Project context
        comes before global because it's strictly more specific to the
        current run.
        """
        identity = self.global_mem.identity.read().strip()
        if identity_chars > 0:
            identity = identity[:identity_chars]
        project_card = self.project.project_card.read().strip()
        if project_chars > 0:
            project_card = project_card[:project_chars]

        project_hits = self.project.relevant_memory_for(
            objective, max_entries=max_project_entries
        )
        global_hits = self.global_mem.relevant_journal_for(
            objective, max_entries=max_global_entries
        )

        if not (identity or project_card or project_hits or global_hits):
            return ""

        lines: list[str] = []
        lines.append("### Memory context (non-authoritative)")
        lines.append(
            "The following identity card, project card, and prior-mission "
            "notes are advisory. If they conflict with the current "
            "objective, the live repo state, or explicit user "
            "instructions, **ignore them**."
        )
        if identity:
            lines.append("")
            lines.append("#### Identity")
            lines.append(identity)
        if project_card:
            lines.append("")
            lines.append(f"#### Project card · {self.project.label}")
            lines.append(project_card)
        if project_hits:
            lines.append("")
            lines.append("#### Possibly-relevant prior runs (this project)")
            for entry in project_hits:
                ts_iso = time.strftime("%Y-%m-%d", time.localtime(entry.ts))
                lines.append(
                    f"- **{ts_iso} · {entry.title}** ({entry.kind}): "
                    f"{entry.summary}"
                )
        if global_hits:
            lines.append("")
            lines.append("#### Possibly-relevant prior runs (other projects)")
            for entry in global_hits:
                ts_iso = time.strftime("%Y-%m-%d", time.localtime(entry.ts))
                lines.append(
                    f"- **{ts_iso} · {entry.title}** ({entry.kind}): "
                    f"{entry.summary}"
                )
        return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------------
# Internal helpers shared by GlobalMemory / ProjectMemory / LifeMemory.
# ---------------------------------------------------------------------------

def _touch_file(path: Path) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return True


def _score_journal(
    journal: Journal,
    objective: str,
    *,
    max_entries: int,
    min_score: float,
    recency_n: int,
) -> list[JournalEntry]:
    recent = journal.tail(recency_n)
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
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [e for _, _, e in scored[:max_entries]]
