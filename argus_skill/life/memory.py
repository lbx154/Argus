"""Persistent memory primitives for life-mode.

Three storage shapes:

- ``Journal``: append-only ``journal.jsonl``. Each entry is one mission
  outcome (or a manually-recorded note). Atomic append via ``O_APPEND``
  on POSIX so concurrent writers don't interleave.
- ``Backlog``: ordered ``backlog.jsonl`` of pending mission objectives.
  Status field on each row toggles ``pending`` → ``running`` → ``done``
  / ``failed`` / ``skipped`` / ``superseded``. We rewrite the whole file on status
  changes; the file is small (tens-to-hundreds of items).
- ``IdentityCard``: a single ``identity.md`` markdown file the user
  edits freely. We just read it.
The :class:`LifeMemory` facade bundles the global files plus a small
retrieval helper that returns the most recent N journal entries as
advisory context. The harness deliberately does NOT score prior missions
for "relevance" to the objective — judging which past work matters is the
agent's job, not the harness's. We surface recent entries (project-scoped)
and let the agent decide; the block is injected non-authoritatively.

This module has **no LLM dependency** so it's testable and importable
in any environment (we use it from the CLI even when the API key is
missing).
"""
from __future__ import annotations

import heapq
import json
import mmap
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from ..core.event_catalog import EventType, canonical_event_type

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
        from ..core.paths import resolve_runtime_path

        return resolve_runtime_path(raw, context="ARGUS_SKILL_LIFE_DIR")
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


def _read_jsonl_tail(
    path: Path,
    n: int,
    *,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
    raw_predicate: Callable[[bytes], bool] | None = None,
    raw_markers: tuple[bytes, ...] | None = None,
) -> list[dict[str, Any]]:
    """Return the last ``n`` matching JSONL rows without a full-file scan."""
    if n <= 0 or not path.exists():
        return []
    if raw_markers:
        return _read_jsonl_tail_marked(
            path,
            n,
            raw_markers=raw_markers,
            predicate=predicate,
        )

    rows_rev: list[dict[str, Any]] = []
    # Sparse filtered tails (EventJournal) may need to walk far back through a
    # busy event log. Larger sequential chunks avoid thousands of tiny reverse
    # seeks while the ordinary unfiltered tail stays lightweight.
    chunk_size = (1024 * 1024) if predicate is not None else (32 * 1024)
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
                    if raw_predicate is not None and not raw_predicate(raw):
                        continue
                    try:
                        row = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if predicate is not None and not predicate(row):
                        continue
                    rows_rev.append(row)
                    if len(rows_rev) >= n:
                        break
            if len(rows_rev) < n:
                raw = buffer.strip()
                if raw:
                    try:
                        if raw_predicate is not None and not raw_predicate(raw):
                            return list(reversed(rows_rev))
                        row = json.loads(raw.decode("utf-8"))
                        if predicate is None or predicate(row):
                            rows_rev.append(row)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        pass
    except OSError:
        return []

    rows_rev.reverse()
    return rows_rev


def _read_jsonl_tail_marked(
    path: Path,
    n: int,
    *,
    raw_markers: tuple[bytes, ...],
    predicate: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    """Find sparse JSONL rows with mmap-backed reverse marker searches.

    Long-running projects retain gigabytes of command/progress events while
    journal and mission-completion events are rare. Broad event-prefix markers
    avoid repeated whole-file scans while only handing candidate lines to
    Python for decoding.
    """
    rows_rev: list[dict[str, Any]] = []
    try:
        with path.open("rb") as fh:
            if fh.seek(0, os.SEEK_END) <= 0:
                return []
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                heap: list[tuple[int, int]] = []
                for index, marker in enumerate(raw_markers):
                    position = mapped.rfind(marker)
                    if position >= 0:
                        heapq.heappush(heap, (-position, index))
                seen_line_starts: set[int] = set()
                while heap and len(rows_rev) < n:
                    negative_position, marker_index = heapq.heappop(heap)
                    position = -negative_position
                    line_start = mapped.rfind(b"\n", 0, position) + 1
                    if line_start not in seen_line_starts:
                        seen_line_starts.add(line_start)
                        line_end = mapped.find(b"\n", position)
                        if line_end < 0:
                            line_end = len(mapped)
                        raw = mapped[line_start:line_end].strip()
                        if raw:
                            try:
                                row = json.loads(raw.decode("utf-8"))
                            except (UnicodeDecodeError, json.JSONDecodeError):
                                row = None
                            if row is not None and (predicate is None or predicate(row)):
                                rows_rev.append(row)
                    marker = raw_markers[marker_index]
                    previous = mapped.rfind(marker, 0, line_start)
                    if previous >= 0:
                        heapq.heappush(heap, (-previous, marker_index))
    except (OSError, ValueError):
        return []
    rows_rev.reverse()
    return rows_rev


def _journal_rollover_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".1")


def _jsonl_history_paths(path: Path) -> list[Path]:
    """All retained JSONL generations, oldest first, then the live file.

    ``JsonlEventSink`` keeps ``.2``, ``.3``, ... as older immutable
    generations while ``.1`` is always the most recent rollover. Budget
    accounting must include all of them; reading only ``.1`` made daily spend
    disappear whenever a noisy project rolled more than once in a day.
    """
    path = Path(path)
    older: list[tuple[int, Path]] = []
    recent: Path | None = None
    prefix = path.name + "."
    try:
        candidates = list(path.parent.glob(prefix + "*"))
    except OSError:
        candidates = []
    for candidate in candidates:
        suffix = candidate.name[len(prefix) :]
        if not suffix.isdigit() or not candidate.is_file():
            continue
        index = int(suffix)
        if index == 1:
            recent = candidate
        elif index >= 2:
            older.append((index, candidate))
    # ``JsonlEventSink`` moves the current .1 to the next free generation on
    # each rollover. Therefore .2 is oldest, followed by .3, .4, ...; .1 is
    # always the newest completed generation.
    paths = [candidate for _index, candidate in sorted(older)]
    if recent is not None:
        paths.append(recent)
    if path.is_file():
        paths.append(path)
    return paths


def _history_signature(paths: Iterable[Path]) -> tuple:
    return tuple((str(path), _path_signature(path)) for path in paths)


def _read_jsonl_history(path: Path) -> list[dict[str, Any]]:
    """Return every retained generation plus the live journal, in order."""
    rows: list[dict[str, Any]] = []
    for history_path in _jsonl_history_paths(path):
        rows.extend(_read_jsonl(history_path))
    return rows


def _read_jsonl_tail_history(
    path: Path,
    n: int,
    *,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
    raw_predicate: Callable[[bytes], bool] | None = None,
    raw_markers: tuple[bytes, ...] | None = None,
    rg_pattern: str | None = None,
) -> list[dict[str, Any]]:
    """Return a filtered tail across every retained generation."""
    if n <= 0:
        return []
    rows: list[dict[str, Any]] = []
    for history_path in reversed(_jsonl_history_paths(path)):
        needed = n - len(rows)
        if needed <= 0:
            break
        fast_rows = _read_jsonl_tail_rg(
            history_path,
            needed,
            pattern=rg_pattern,
            predicate=predicate,
        ) if rg_pattern else None
        rows = (
            fast_rows
            if fast_rows is not None
            else _read_jsonl_tail(
                history_path,
                needed,
                predicate=predicate,
                raw_predicate=raw_predicate,
                raw_markers=raw_markers,
            )
        ) + rows
    return rows


def _read_jsonl_tail_rg(
    path: Path,
    n: int,
    *,
    pattern: str | None,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]] | None:
    """Optional ripgrep fast path for very sparse events in huge JSONL files."""
    rg = shutil.which("rg")
    if not pattern or rg is None or n <= 0 or not path.is_file():
        return None
    try:
        result = subprocess.run(
            [rg, "--text", "--no-heading", "--no-filename", "--color", "never", "-e", pattern, str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode not in {0, 1}:
        return None
    rows: deque[dict[str, Any]] = deque(maxlen=n)
    for raw in result.stdout.splitlines():
        try:
            row = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if predicate is None or predicate(row):
            rows.append(row)
    return list(rows)


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
            cost_usd=float(row.get("cost_usd", 0.0) or 0.0),
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


class EventJournal(Journal):
    """Journal API backed by the canonical ``events.jsonl`` timeline.

    ``memory.jsonl`` used to be a second, parallel truth surface. Project memory
    now reads concrete event types from ``events.jsonl``. The old
    ``JournalEntry`` write API is retired; ``journal.entry`` is accepted only as a
    legacy read shape and is never written.
    """

    LEGACY_EVENT_TYPE = "journal.entry"
    JOURNAL_EVENT_TYPES = {
        "journal.entry",
        EventType.LIFE_MISSION_STARTED,
        EventType.LIFE_MISSION_COMPLETED,
        EventType.LIFE_PLANNER_VERDICT,
        EventType.LIFE_PLANNER_ERROR,
        EventType.LIFE_PLANNER_WAITING,
        EventType.LIFE_BUDGET_PAUSE,
        EventType.LIFE_LIFECYCLE_BLOCK,
        "user.note",
    }
    _RAW_EVENT_MARKERS = tuple(
        marker.encode("utf-8")
        for marker in (
            "journal_kind",
            "life.",
            "user.note",
            "journal.entry",
            "mission.",
        )
    )
    _RG_PATTERN = (
        r'"(?:type|canonical_type)"\s*:\s*"(?:journal\.entry|user\.note|'
        r'mission\.(?:started|completed)|life\.(?:mission|planner|budget|lifecycle)\.[^"]+)"'
        r'|"journal_kind"\s*:'
    )

    def append(self, entry: JournalEntry) -> None:  # noqa: ARG002
        """Retired write API: project journals are derived from events only."""
        return None

    @classmethod
    def _is_journal_event(cls, row: dict[str, Any]) -> bool:
        event_type = canonical_event_type(row.get("canonical_type") or row.get("type"))
        return bool(row.get("journal_kind")) or event_type in cls.JOURNAL_EVENT_TYPES

    @classmethod
    def _might_be_journal_event(cls, raw: bytes) -> bool:
        """Cheaply reject ordinary progress lines before expensive JSON decoding."""
        return any(marker in raw for marker in cls._RAW_EVENT_MARKERS)

    @staticmethod
    def _entry_from_event(row: dict[str, Any]) -> JournalEntry | None:
        if row.get("journal_kind") or row.get("type") == EventJournal.LEGACY_EVENT_TYPE:
            return JournalEntry.from_jsonable(row)
        etype = canonical_event_type(row.get("canonical_type") or row.get("type"))
        if etype == EventType.LIFE_PLANNER_VERDICT:
            from ..core.planner_verdict import (
                PlannerVerdictStatus,
                adapt_legacy_planner_verdict_event,
            )

            known_statuses = {status.value for status in PlannerVerdictStatus}
            if str(row.get("status") or "") not in known_statuses:
                row = adapt_legacy_planner_verdict_event(row)
        # The mid-mission budget breaker emits ``life.mission.completed`` with
        # ``status="paused_budget"`` (success=False). Derive the documented
        # ``budget_pause`` kind rather than mislabeling it ``mission_failed`` —
        # the generic completed→kind map below keys only on ``success``.
        if (
            etype == EventType.LIFE_MISSION_COMPLETED
            and str(row.get("status") or "") in {"budget_pause", "paused_budget"}
        ):
            kind: str | None = "budget_pause"
        elif (
            etype == EventType.LIFE_MISSION_COMPLETED
            and str(row.get("status") or "") == "replan_requested"
        ):
            kind = "mission_replan_requested"
        elif (
            etype == EventType.LIFE_MISSION_COMPLETED
            and str(row.get("status") or "") in {
                "paused_provider_cooldown",
                "paused_provider_fence",
            }
        ):
            kind = "provider_pause"
        elif (
            etype == EventType.LIFE_MISSION_COMPLETED
            and str(row.get("status") or "") in {
                "research_incomplete",
                "paused_no_breakthrough",
                "exhausted_current_methods",
                "infra_blocked",
            }
        ):
            kind = "research_pause"
        else:
            kind = {
                EventType.LIFE_MISSION_STARTED: "mission_started",
                EventType.LIFE_MISSION_COMPLETED: (
                    "mission_complete" if row.get("success", True) else "mission_failed"
                ),
                EventType.LIFE_PLANNER_VERDICT: "planner_cycle",
                EventType.LIFE_PLANNER_ERROR: "planner_error",
                EventType.LIFE_PLANNER_WAITING: "planner_waiting",
                EventType.LIFE_BUDGET_PAUSE: "budget_pause",
                EventType.LIFE_LIFECYCLE_BLOCK: "lifecycle_block",
                "user.note": "user_note",
            }.get(etype)
        if kind is None:
            return None
        return JournalEntry.from_jsonable({
            "id": str(row.get("id") or row.get("item_id") or uuid.uuid4().hex[:12]),
            "ts": row.get("ts", time.time()),
            "kind": kind,
            "title": row.get("title") or row.get("objective") or etype,
            "summary": row.get("summary") or row.get("reason") or row.get("text") or "",
            "tags": row.get("tags") or [etype],
            "cost_usd": row.get("cost_usd", 0.0),
            "extra": row,
        })

    def _rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for event in _read_jsonl_history(self.path):
            entry = self._entry_from_event(event)
            if entry is not None:
                rows.append(entry.to_jsonable())
        return rows

    def all(self) -> list[JournalEntry]:
        return [JournalEntry.from_jsonable(r) for r in self._rows()]

    def tail(self, n: int = 20) -> list[JournalEntry]:
        if n <= 0:
            return []
        events = _read_jsonl_tail_history(
            self.path,
            n,
            predicate=self._is_journal_event,
            raw_predicate=self._might_be_journal_event,
            raw_markers=self._RAW_EVENT_MARKERS,
            rg_pattern=self._RG_PATTERN,
        )
        return [
            entry for row in events
            if (entry := self._entry_from_event(row)) is not None
        ]

    def total_cost_since(self, ts: float) -> float:
        paths = _jsonl_history_paths(self.path)
        signature = _history_signature(paths)
        cached = self._total_cost_cache.get(ts)
        if cached is not None and cached[0] == signature:
            return cached[1]
        total = 0.0
        for path in paths:
            # Closed rollover files older than the requested window cannot
            # contain a newer event, so skip them without parsing hundreds of
            # megabytes of agent I/O.
            try:
                if path != self.path and path.stat().st_mtime < ts:
                    continue
            except OSError:
                continue
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for raw in fh:
                        try:
                            row = json.loads(raw)
                        except (json.JSONDecodeError, ValueError):
                            continue
                        if not isinstance(row, dict) or not self._is_journal_event(row):
                            continue
                        try:
                            row_ts = float(row.get("ts", 0.0))
                            cost = float(row.get("cost_usd", 0.0))
                        except (TypeError, ValueError):
                            continue
                        if row_ts >= ts:
                            total += cost
            except OSError:
                continue
        self._total_cost_cache[ts] = (signature, total)
        return total


# ---------------------------------------------------------------------------
# Backlog
# ---------------------------------------------------------------------------

_BACKLOG_STATUSES = {
    "pending",
    "running",
    "paused",
    "paused_budget",
    "paused_provider_cooldown",
    "paused_provider_fence",
    "research_incomplete",
    "paused_no_breakthrough",
    "exhausted_current_methods",
    "infra_blocked",
    "done",
    "failed",
    "skipped",
    "superseded",
}
_TERMINAL_STATUSES = {"done", "failed", "skipped", "superseded"}
_RECOVERABLE_PAUSE_STATUSES = {
    "paused",
    "paused_budget",
    "paused_provider_cooldown",
    "paused_provider_fence",
    "research_incomplete",
    "paused_no_breakthrough",
    "exhausted_current_methods",
    "infra_blocked",
}


class IllegalStateTransition(RuntimeError):
    """Raised when a status update would resurrect a terminal item.

    Defensive against the entire class of bugs where a code path
    accidentally re-runs an already-completed mission. ``done``, ``failed``,
    ``skipped``, and ``superseded`` are sinks: the only way to get a
    new attempt at the same work is to enqueue a fresh
    :class:`BacklogItem` (so it gets a new id and audit trail).
    """


@dataclass(frozen=True)
class PlanRevisionResult:
    superseded_ids: tuple[str, ...]
    added_ids: tuple[str, ...]


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
    # Set when this item's terminal reviewer verdict was "blocked" with a
    # non-empty ``operator_question`` (reviewer_schema.json) — i.e. it did not
    # fail on a bug/crash, it stopped because the REVIEWER needed the operator
    # to make a call. Persisted on the item (not just kept in an ephemeral
    # Manager state) so it survives a cockpit/daemon restart and so /status can
    # list every currently-unanswered question, not just the single most
    # recent one. Cleared back to "" once the operator's answer has been
    # folded into a follow-up item (see ``manager.dispatch.enqueue_mission``)
    # — a non-empty value always means "still waiting on the operator".
    pending_question: str = ""
    # --- iteration loop fields (Phase-7) -------------------------------
    # When ``iterate`` is True the supervisor, after a successful
    # ``done`` verdict, hands the produced artefacts to a L2 reviewer agent. The reviewer is the only verdict authority;
    # there is no separate critic polish layer any more.
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
    # Usage is metered per attempt while the backlog item and its checkpoint,
    # method ledger, and skill-adaptation state keep the stable item id.
    attempt: int = 1
    # --- dependency DAG (topological scheduling) -----------------------
    # ``deps`` is the list of *other* backlog item ids this item depends
    # on. An item is only claimable once **every** dep has reached the
    # terminal ``done`` status. An empty ``deps`` (the default — and the
    # shape of every pre-DAG row) means "no dependencies", so the item is
    # always ready and the legacy flat-backlog behaviour is preserved
    # bit-for-bit.
    deps: list[str] = field(default_factory=list)
    # --- dynamic plan identity and progressive context -----------------
    # The supervisor assigns opaque plan identity/version values; the Planner
    # authors only node_key, dependencies, and context references.
    plan_id: str = ""
    plan_version: int = 0
    node_key: str = ""
    context_refs: list[dict[str, str]] = field(default_factory=list)
    superseded_by_plan_id: str = ""
    superseded_reason: str = ""

    @classmethod
    def new_id(cls) -> str:
        return uuid.uuid4().hex[:12]

    @classmethod
    def new(
        cls,
        *,
        title: str,
        objective: str,
        item_id: str | None = None,
        priority: int = 100,
        max_cost_usd: float = 30.0,
        tags: list[str] | None = None,
        notes: str = "",
        iterate: bool = True,
        iteration_max_cycles: int = 6,
        iteration_budget_usd: float = 30.0,
        deps: list[str] | None = None,
        plan_id: str = "",
        plan_version: int = 0,
        node_key: str = "",
        context_refs: list[dict[str, str]] | None = None,
    ) -> "BacklogItem":
        objective = objective.strip()
        return cls(
            id=str(item_id or cls.new_id()),
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
            deps=list(deps or []),
            plan_id=str(plan_id),
            plan_version=max(0, int(plan_version)),
            node_key=str(node_key),
            context_refs=[
                {str(key): str(value) for key, value in ref.items()}
                for ref in (context_refs or [])
                if isinstance(ref, dict)
            ],
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
            pending_question=str(row.get("pending_question", "")),
            iterate=bool(row.get("iterate", False)),
            iteration_max_cycles=int(row.get("iteration_max_cycles", 6)),
            iteration_budget_usd=float(row.get("iteration_budget_usd", 30.0)),
            iteration_cycles_done=int(row.get("iteration_cycles_done", 0)),
            iteration_cost_usd=float(row.get("iteration_cost_usd", 0.0)),
            original_objective=str(row.get("original_objective", objective)),
            orphan_retries=int(row.get("orphan_retries", 0)),
            attempt=max(1, int(row.get("attempt", 1) or 1)),
            # Pre-DAG rows have no ``deps`` key → []. An empty dep list
            # means "always ready", so old backlogs schedule exactly as
            # they did before the DAG upgrade.
            deps=list(row.get("deps", [])),
            plan_id=str(row.get("plan_id", "")),
            plan_version=max(0, int(row.get("plan_version", 0) or 0)),
            node_key=str(row.get("node_key", "")),
            context_refs=[
                {str(key): str(value) for key, value in ref.items()}
                for ref in (row.get("context_refs", []) or [])
                if isinstance(ref, dict)
            ],
            superseded_by_plan_id=str(row.get("superseded_by_plan_id", "")),
            superseded_reason=str(row.get("superseded_reason", "")),
        )


class Backlog:
    """Ordered persistent backlog of missions, scheduled as a DAG.

    Pending items are sorted by ``(priority asc, ts asc)`` so callers can
    always ``next_pending()`` to get the head. Each item may declare
    ``deps`` (ids of other items); an item is only claimable once every
    dep has reached ``done``. Items with no deps are always ready, so a
    flat (dep-less) backlog behaves exactly as it did before the DAG
    upgrade. A pending item whose dependency reaches a terminal-non-done
    state (``failed`` / ``skipped`` / ``superseded`` / missing) is
    cascade-skipped on the
    next ``claim_next`` so a dead dependency can't wedge the queue.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock_path = self.path.parent / f"{self.path.name}.lock"

    # --- io ---
    def _load(self) -> list[BacklogItem]:
        return [BacklogItem.from_jsonable(r) for r in _read_jsonl(self.path)]

    def _save(self, items: Iterable[BacklogItem]) -> None:
        _atomic_rewrite_jsonl(self.path, (it.to_jsonable() for it in items))

    @staticmethod
    def _done_ids(items: Iterable[BacklogItem]) -> set[str]:
        """Ids of items that have completed successfully.

        Only ``done`` counts as a satisfied dependency. ``failed`` /
        ``skipped`` are terminal but *not* satisfied — a dependent of a
        failed item can never run and is cascade-skipped (see
        :meth:`_cascade_blocked`). Mirrors ``team/task_board._done_ids``.
        """
        return {it.id for it in items if it.status == "done"}

    @staticmethod
    def _is_ready(item: BacklogItem, done: set[str]) -> bool:
        """A pending item is ready iff every dep is in ``done``.

        ``all(... for ... in [])`` is ``True``, so a dep-less item is
        always ready — this is what guarantees the no-deps behaviour is
        identical to the pre-DAG flat backlog.
        """
        return item.status == "pending" and all(d in done for d in item.deps)

    def _cascade_blocked(self, items: list[BacklogItem]) -> bool:
        """Skip pending items whose deps can never all become ``done``.

        A pending item that lists a dep already in a terminal-but-not-done
        state (``failed`` / ``skipped`` / ``superseded``) can never satisfy its
        dependency
        set, so it would wait forever and look like permanently-blocked
        work to the supervisor. We mark such items ``skipped`` with an
        explanatory ``last_error`` so the dead dependency clears itself
        and the daemon's idle logic behaves as if there is simply no
        ready work.

        Returns ``True`` if any item was mutated (caller must ``_save``).
        Must run inside ``_locked``.
        """
        by_id = {it.id: it for it in items}
        changed = False
        for it in items:
            if it.status != "pending":
                continue
            for dep_id in it.deps:
                dep = by_id.get(dep_id)
                # A dep that resolves to a terminal-non-done state (or to a
                # missing item, which can also never become ``done``) blocks
                # this item forever. Self/cyclic deps stay pending (never
                # ready, never cascaded) — they cannot deadlock the daemon
                # because a never-ready item is indistinguishable from "no
                # work", which the idle path already handles.
                if dep is None:
                    it.status = "skipped"
                    it.finished_ts = time.time()
                    it.last_error = (
                        f"blocked: dependency {dep_id} does not exist"
                    )
                    changed = True
                    break
                if dep.status in _TERMINAL_STATUSES and dep.status != "done":
                    it.status = "skipped"
                    it.finished_ts = time.time()
                    it.last_error = (
                        f"blocked: dependency {dep_id} did not complete "
                        f"({dep.status})"
                    )
                    changed = True
                    break
        return changed

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

    def apply_plan_revision(
        self,
        *,
        expected_plan_id: str,
        expected_version: int,
        new_plan_id: str,
        new_version: int,
        supersede_item_ids: Iterable[str],
        new_items: Iterable[BacklogItem],
        reason: str,
    ) -> PlanRevisionResult:
        """Atomically replace every active item in one plan revision."""
        expected_version = int(expected_version)
        new_version = int(new_version)
        reason = str(reason).strip()
        supersede_ids = tuple(dict.fromkeys(str(item_id) for item_id in supersede_item_ids))
        replacements = list(new_items)
        if not reason:
            raise ValueError("plan revision reason must not be empty")
        if not new_plan_id or new_plan_id == expected_plan_id:
            raise ValueError("replacement plan id must be new and non-empty")
        if new_version != expected_version + 1:
            raise ValueError("replacement plan version must increment by one")
        if not replacements:
            raise ValueError("replacement plan must contain at least one item")

        replacement_ids = [item.id for item in replacements]
        if len(replacement_ids) != len(set(replacement_ids)):
            raise ValueError("replacement plan contains duplicate item ids")
        replacement_keys = [item.node_key for item in replacements]
        if any(not key for key in replacement_keys):
            raise ValueError("replacement plan node keys must not be empty")
        if len(replacement_keys) != len(set(replacement_keys)):
            raise ValueError("replacement plan contains duplicate node keys")
        replacement_id_set = set(replacement_ids)

        with self._locked():
            items = self._load()
            active_ids = {
                item.id
                for item in items
                if item.plan_id == expected_plan_id
                and item.plan_version == expected_version
                and item.status not in _TERMINAL_STATUSES
            }
            if not active_ids:
                raise RuntimeError(
                    "plan revision conflict: expected active plan revision not found"
                )
            existing_ids = {item.id for item in items}
            if replacement_id_set & existing_ids:
                raise ValueError("replacement plan reuses an existing backlog item id")
            for item in replacements:
                if item.status != "pending":
                    raise ValueError("replacement plan items must start pending")
                if item.plan_id != new_plan_id or item.plan_version != new_version:
                    raise ValueError(
                        "replacement item plan identity does not match revision"
                    )
                outside = [dep for dep in item.deps if dep not in replacement_id_set]
                if outside:
                    raise ValueError(
                        "replacement item dependency points outside the replacement batch"
                    )
            if set(supersede_ids) != active_ids:
                raise ValueError(
                    "plan revision must supersede every active item in the expected plan"
                )

            now = time.time()
            for item in items:
                if item.id not in active_ids:
                    continue
                item.status = "superseded"
                item.finished_ts = now
                item.superseded_by_plan_id = new_plan_id
                item.superseded_reason = reason
            items.extend(replacements)
            self._save(items)

        return PlanRevisionResult(
            superseded_ids=supersede_ids,
            added_ids=tuple(replacement_ids),
        )

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
                        if (
                            it.status in _RECOVERABLE_PAUSE_STATUSES
                            and new_status == "pending"
                        ):
                            raise IllegalStateTransition(
                                f"backlog item {item_id} is paused; use "
                                "resume_paused() so the usage attempt advances"
                            )
                    for k, v in fields.items():
                        if hasattr(it, k):
                            setattr(it, k, v)
                    out = it
                    break
            if out is not None:
                self._save(items)
            return out

    def continue_with_operator_reply(
        self,
        item_id: str,
        answer: str,
    ) -> tuple[BacklogItem | None, BacklogItem | None]:
        """Atomically consume one pending question and enqueue its continuation."""
        with self._locked():
            items = self._load()
            blocked = next((item for item in items if item.id == item_id), None)
            if blocked is None or not str(blocked.pending_question or "").strip():
                return blocked, None
            continuation = BacklogItem.new(
                title=blocked.title,
                objective=(
                    f"{blocked.objective.strip()}\n\n"
                    f"Operator reply to blocked question:\n{answer.strip()}"
                ),
                priority=blocked.priority,
                max_cost_usd=blocked.max_cost_usd,
                tags=[*blocked.tags, "operator-reply"],
                notes=f"Continues blocked item {blocked.id}.",
                iterate=blocked.iterate,
                iteration_max_cycles=blocked.iteration_max_cycles,
                iteration_budget_usd=blocked.iteration_budget_usd,
            )
            blocked.pending_question = ""
            items.append(continuation)
            self._save(items)
            return blocked, continuation

    def claim_next(self) -> BacklogItem | None:
        """Atomically pick the head *ready* pending item and flip it to ``running``.

        Replaces the ``next_pending()`` + ``mark_running()`` pair so the
        TOCTOU window between "see a pending row" and "claim it" closes.
        Returns the claimed item (with its in-memory ``status`` already
        ``running`` and ``started_ts`` set), or ``None`` if nothing is
        *ready* (no pending item whose deps are all ``done``). We rewrite
        the file under the same lock that ``_save`` already uses, so two
        concurrent callers cannot both win.

        Dependency DAG: an item is only eligible once every id in its
        ``deps`` is ``done``. A dep-less item (``deps == []``) is always
        eligible, so flat backlogs schedule exactly as before. Before
        choosing, we cascade-skip any pending item whose dep reached a
        terminal-non-done state so a dead dependency cannot wedge the
        queue forever.
        """
        with self._locked():
            items = self._load()
            # Clear dead dependencies first (failed/skipped/missing dep →
            # the dependent can never run). Persist the skip so the
            # supervisor doesn't keep re-seeing a permanently-blocked item.
            cascaded = self._cascade_blocked(items)
            done = self._done_ids(items)
            ready = [it for it in items if self._is_ready(it, done)]
            if not ready:
                if cascaded:
                    self._save(items)
                return None
            ready.sort(key=lambda it: (it.priority, it.ts))
            head = ready[0]
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

    def resume_paused(self, item_id: str) -> BacklogItem | None:
        """Start a fresh metering attempt for one recoverable paused item."""
        with self._locked():
            items = self._load()
            out: BacklogItem | None = None
            for item in items:
                if item.id != item_id:
                    continue
                if item.status not in _RECOVERABLE_PAUSE_STATUSES:
                    return None
                item.status = "pending"
                item.attempt = max(1, int(item.attempt or 1)) + 1
                item.started_ts = None
                item.finished_ts = None
                out = item
                break
            if out is not None:
                self._save(items)
            return out

    def resume_all_paused(self) -> list[BacklogItem]:
        """Atomically re-arm every recoverable pause with fresh usage attempts."""
        with self._locked():
            items = self._load()
            resumed: list[BacklogItem] = []
            for item in items:
                if item.status not in _RECOVERABLE_PAUSE_STATUSES:
                    continue
                item.status = "pending"
                item.attempt = max(1, int(item.attempt or 1)) + 1
                item.started_ts = None
                item.finished_ts = None
                resumed.append(item)
            if resumed:
                self._save(items)
            return resumed

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

    def ready(self) -> list[BacklogItem]:
        """Pending items whose deps are all ``done``, head-ordered.

        This is the dependency-aware counterpart to :meth:`pending`:
        ``pending`` lists every un-started item (for display / status);
        ``ready`` lists only the ones actually claimable right now. A
        dep-less item is always ready, so for a flat (no-deps) backlog
        ``ready()`` and ``pending()`` return the same list.
        """
        items = self._load()
        done = self._done_ids(items)
        out = [it for it in items if self._is_ready(it, done)]
        out.sort(key=lambda it: (it.priority, it.ts))
        return out

    def next_pending(self) -> BacklogItem | None:
        """Head of the *ready* queue (deps all ``done``), or ``None``.

        Kept named ``next_pending`` for the existing supervisor call
        sites. It now returns the next *claimable* item rather than the
        next merely-pending one, so it stays consistent with
        :meth:`claim_next`: when no item is ready, both report "nothing
        to run", which the supervisor's idle path already handles. This
        is a pure read (no cascade mutation); dead-dependency cleanup
        happens in the write-locked :meth:`claim_next`.
        """
        rdy = self.ready()
        return rdy[0] if rdy else None


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
- Read this card before each mission.
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
#
# Recency-only. The harness used to score prior missions by keyword-Jaccard
# overlap against the objective, but lexical "overlap = relevance" is a
# judgment the harness has no business making: it can drop a semantically
# relevant entry that shares no keywords, or surface lexically-similar noise.
# Relevance is the agent's call. We hand it the most recent entries (within a
# bounded window, project-scoped by which journal is passed) as advisory
# context and let it decide.


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
            journal=EventJournal(root / "events.jsonl"),
            backlog=Backlog(root / "backlog.jsonl"),
        )

    def init(self) -> dict[str, bool]:
        """Idempotently seed the directory; returns what was created."""
        self.root.mkdir(parents=True, exist_ok=True)
        return {
            "identity": self.identity.ensure_default(),
            "events": self._touch(self.journal.path),
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

    def recent_journal(
        self,
        *,
        max_entries: int = 3,
        recency_n: int = 30,
    ) -> list[JournalEntry]:
        """Return the newest journal entries as non-authoritative context."""
        return _recent_journal(
            self.journal,
            max_entries=max_entries,
            recency_n=recency_n,
        )

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def render_prelude(
        self,
        *,
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
        relevant = self.recent_journal(max_entries=max_journal_entries)

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
            lines.append("#### Recent prior missions")
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
# * :class:`GlobalMemory` — agent-wide identity card and operator audit
#   journal under ``~/.argus-skill/``.
# * :class:`ProjectMemory` — per-project event log and backlog under
#   ``~/.argus-skill/projects/<fingerprint>/``. Lazy-created on
#   first access so we don't litter ``projects/`` with empty trees.
#
# :class:`MemoryBundle` is a thin convenience wrapper that holds one of
# each plus a unified :meth:`render_prelude` that merges global identity
# and relevant entries from the current project's event history.
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
    """Agent-wide identity and shared skills under ``~/.argus-skill/``.

    The directory is *lazy*: nothing is written until you call
    :meth:`init` (idempotent) or perform a write through one of the
    sub-objects (which create their parent dirs on demand).
    """

    root: Path
    identity: IdentityCard

    @classmethod
    def open(cls, root: Path | None = None) -> "GlobalMemory":
        actual = Path(root) if root is not None else _resolve_global_root()
        return cls(
            root=actual,
            identity=IdentityCard(actual / "identity.md"),
        )

    def init(self) -> dict[str, bool]:
        """Idempotently seed the global directory; returns core files created.

        Bundled default skills are also seeded into ``<root>/skills`` as a
        side effect. Logs are per-project
        (``projects/<fingerprint>/events.jsonl``); there is no global journal.
        """
        from ..skills.builtins import seed_builtin_skills

        self.root.mkdir(parents=True, exist_ok=True)
        seed_builtin_skills(self.root / "skills")
        return {
            "identity": self.identity.ensure_default(),
        }

@dataclass
class ProjectMemory:
    """Per-project memory under ``~/.argus-skill/projects/<fingerprint>/``.

    Holds two things:

    * ``memory`` — journal API backed by the canonical per-project
      ``events.jsonl`` timeline. ``memory.jsonl`` is no longer created.
    * ``backlog`` — pending mission queue scoped to this project.
    """

    fingerprint: str
    label: str
    root: Path
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
            memory=EventJournal(root / "events.jsonl"),
            backlog=Backlog(root / "backlog.jsonl"),
        )

    def init(self) -> dict[str, bool]:
        """Create the project directory + seed defaults if missing."""
        self.root.mkdir(parents=True, exist_ok=True)
        return {
            "events": _touch_file(self.memory.path),
            "backlog": _touch_file(self.backlog.path),
        }

    def recent_journal(
        self,
        *,
        max_entries: int = 3,
        recency_n: int = 30,
    ) -> list[JournalEntry]:
        return _recent_journal(
            self.memory,
            max_entries=max_entries,
            recency_n=recency_n,
        )

@dataclass
class MemoryBundle:
    """Bundles one :class:`GlobalMemory` plus one :class:`ProjectMemory`.

    The reviewer prompt builder and unified cockpit both want
    "everything the agent knows about this run" in one object instead
    of juggling two facades. Construct via :meth:`for_cwd` to get
    automatic project-fingerprint resolution.
    """

    global_mem: GlobalMemory
    project: ProjectMemory
    project_worktree: Path | None = None

    @property
    def root(self) -> Path:
        return self.global_mem.root

    @property
    def global_root(self) -> Path:
        return self.global_mem.root

    @property
    def project_root(self) -> Path:
        return self.project.root

    @property
    def identity(self) -> IdentityCard:
        return self.global_mem.identity

    @property
    def backlog(self) -> Backlog:
        return self.project.backlog

    @property
    def journal(self) -> Journal:
        """The active journal view for this run.

        Writes and reads land as concrete typed events with a ``journal_kind``
        field in the canonical ``projects/<fingerprint>/events.jsonl`` timeline.
        Nothing is mirrored to a global journal, so no cross-project audit trail
        accumulates.
        """
        return self.project.memory

    @classmethod
    def for_cwd(
        cls,
        cwd: Path | str | None = None,
        *,
        global_root: Path | None = None,
        fingerprint: str | None = None,
        label: str | None = None,
    ) -> "MemoryBundle":
        """Open the memory bundle for a project.

        Default (``fingerprint=None``): identity derives from the cwd /
        git-remote (legacy behaviour, unchanged). When ``fingerprint`` is
        given (e.g. a session id), it keys ``projects/<fingerprint>/``
        directly — the session model passes the resolved session id here so a
        fresh ``argus-skill`` opens a NEW project regardless of cwd.
        """
        from ..core.project import project_fingerprint  # local: avoid cycle

        if fingerprint is None:
            identity = project_fingerprint(cwd)
            fingerprint = identity.fingerprint
            label = label or identity.label
            worktree = Path(identity.cwd)
        else:
            worktree = Path(cwd).resolve() if cwd else Path.cwd().resolve()
            label = label or fingerprint
        return cls(
            global_mem=GlobalMemory.open(global_root),
            project=ProjectMemory.open(
                fingerprint,
                label=label,
                global_root=global_root,
            ),
            project_worktree=worktree,
        )

    def init(self) -> dict[str, dict[str, bool]]:
        return {
            "global": self.global_mem.init(),
            "project": self.project.init(),
        }

    def render_prelude(
        self,
        *,
        identity_chars: int = 600,
        max_project_entries: int = 3,
    ) -> str:
        """Render a unified memory prelude for prompt injection.

        Order is: global identity → recent project memories. Cross-project
        journal entries are intentionally excluded:
        workspace prompts must not satisfy or steer the current mission with
        artifacts from another project.
        """
        identity = self.global_mem.identity.read().strip()
        if identity_chars > 0:
            identity = identity[:identity_chars]

        project_hits = self.project.recent_journal(max_entries=max_project_entries)

        if not (identity or project_hits):
            return ""

        lines: list[str] = []
        lines.append("### Memory context (non-authoritative)")
        lines.append(
            "The following identity card and prior-mission "
            "notes are advisory. If they conflict with the current "
            "objective, the live repo state, or explicit user "
            "instructions, **ignore them**."
        )
        if identity:
            lines.append("")
            lines.append("#### Identity")
            lines.append(identity)
        if project_hits:
            lines.append("")
            lines.append("#### Recent prior runs (this project)")
            for entry in project_hits:
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


def _recent_journal(
    journal: Journal,
    *,
    max_entries: int,
    recency_n: int,
) -> list[JournalEntry]:
    # Return the most recent entries (newest first), bounded by both
    # ``recency_n`` (how far back to look) and ``max_entries`` (how many to
    # surface).
    recent = journal.tail(recency_n)
    if not recent:
        return []
    # tail() yields oldest→newest; surface newest first.
    return list(reversed(recent))[:max_entries]
