"""Server-side expansion of Atlas card references quoted by the operator.

The Atlas map composer lets the operator quote a card/step into a message as
one marker line — ``[[Argus引用 {json}]]`` — where the JSON is the frontend's
``CardReference`` (``frontend/web/src/map/presentation.ts``). Until this
module existed nothing on the backend parsed that line: the Manager received
raw JSON.

:func:`expand_operator_references` turns each valid marker line into

- a readable inline line in the visible/persisted operator text
  (``（引用：《<task title>》· <step title>）``), and
- one entry in a bounded, secret-redacted context block
  (``## 操作员引用的地图节点``) that the caller appends after the operator's
  words on the model-facing path only.

A malformed marker line is left byte-for-byte untouched (mirror of the
frontend ``splitDraft`` validation), and a reference to an unknown task keeps
only the inline line. Task data comes from the same backlog reader the map
projection uses (``LifeMemory.open(life_dir).backlog.history()``); event text
comes from the events.jsonl 8 MiB tail with the same normalized ids and the
same secret redaction (``core.secret_guard.redact_secrets_text``) as
``map_view``.

The module also computes which referenced tasks a NEW backlog item may list
as ``deps``: only tasks that exist and whose status can still satisfy a
dependency (``done`` or a live status). A dep on a task in a terminal state
other than ``done`` would be cascade-skipped by ``life.memory`` scheduling,
so those references are reported in ``skipped_dep_task_ids`` and explained in
the context block instead.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.secret_guard import redact_secrets_text
from ..life.memory import _TERMINAL_STATUSES

log = logging.getLogger(__name__)

REFERENCE_MARKER_PREFIX = "[[Argus引用 "
REFERENCE_MARKER_SUFFIX = "]]"

CONTEXT_HEADER = "## 操作员引用的地图节点"
CONTEXT_HEADER_EN = "## Map nodes the operator referenced"
CONTEXT_BLOCK_MAX_CHARS = 4096
MAX_EXPANDED_REFERENCES = 4
MAX_EVENTS_PER_REFERENCE = 3
EVENT_TEXT_CLIP = 600
OBJECTIVE_CLIP = 300
TITLE_CLIP = 120

_EVENT_TAIL_BYTES = 8 * 1024 * 1024

# Statuses that can never satisfy a dependency: a pending item that lists one
# of these as a dep is skipped by ``memory._cascade_blocked``.
_UNSATISFIABLE_STATUSES = frozenset(_TERMINAL_STATUSES) - {"done"}


@dataclass(frozen=True)
class CardReference:
    """One quoted map node, validated to the frontend serializer's shape."""

    source: str
    task_id: str
    task_title: str
    event_ids: tuple[str, ...] = ()
    part: int | None = None
    step_id: str = ""
    step_title: str = ""
    team_id: str = ""
    team_task_id: str = ""
    # Locale the operator quoted under; old markers carry none and stay zh.
    lang: str = "zh"


@dataclass
class ReferenceExpansion:
    """Result of expanding one operator message."""

    text: str
    context_block: str = ""
    references: list[CardReference] = field(default_factory=list)
    dep_task_ids: list[str] = field(default_factory=list)
    skipped_dep_task_ids: list[str] = field(default_factory=list)
    matched: bool = False


def parse_card_reference(line: str) -> CardReference | None:
    """Parse one ``[[Argus引用 {...}]]`` line, or ``None`` when malformed.

    Mirrors the frontend ``splitDraft`` validation exactly: any shape
    violation means the line is not a reference and must stay untouched.
    """
    # Frontend `splitDraft` requires the marker to START the line; an indented
    # marker stays raw there, so it must stay raw here too or the two views of
    # the same message diverge.
    candidate = line.rstrip()
    if not (
        candidate.startswith(REFERENCE_MARKER_PREFIX)
        and candidate.endswith(REFERENCE_MARKER_SUFFIX)
        and len(candidate)
        > len(REFERENCE_MARKER_PREFIX) + len(REFERENCE_MARKER_SUFFIX)
    ):
        return None
    payload = candidate[len(REFERENCE_MARKER_PREFIX):-len(REFERENCE_MARKER_SUFFIX)]
    try:
        raw = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    for key in ("source", "task_id", "task_title"):
        if not isinstance(raw.get(key), str):
            return None
    part = raw.get("part")
    if part is not None and (
        isinstance(part, bool) or not isinstance(part, int) or part <= 0
    ):
        return None
    for key in ("step_id", "step_title", "team_id", "team_task_id", "lang"):
        value = raw.get(key)
        if value is not None and not isinstance(value, str):
            return None
    event_ids = raw.get("event_ids")
    if not isinstance(event_ids, list) or not all(
        isinstance(item, str) for item in event_ids
    ):
        return None
    return CardReference(
        source=raw["source"],
        task_id=raw["task_id"],
        task_title=raw["task_title"],
        event_ids=tuple(event_ids),
        part=part,
        step_id=str(raw.get("step_id") or ""),
        step_title=str(raw.get("step_title") or ""),
        team_id=str(raw.get("team_id") or ""),
        team_task_id=str(raw.get("team_task_id") or ""),
        lang="en" if raw.get("lang") == "en" else "zh",
    )


def _clip(value: str, limit: int) -> str:
    compact = " ".join(str(value or "").split())
    compact = redact_secrets_text(compact)
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


def _backlog_by_id(life_dir: Path) -> dict[str, Any]:
    """The same server-side backlog view the map projection reads."""
    from ..life.memory import LifeMemory

    try:
        return {item.id: item for item in LifeMemory.open(life_dir).backlog.history()}
    except Exception:  # noqa: BLE001 — a quoted card must never break the turn
        log.exception("map references: backlog read failed for %s", life_dir)
        return {}


def _event_texts_by_id(life_dir: Path, wanted: set[str]) -> dict[str, str]:
    """Resolve map event ids to their text from the events.jsonl tail.

    Uses the map projection's own id scheme — the raw ``event_id`` field when
    present, else ``map_view.digest(row)`` for rows the map would surface — so
    the ids the frontend copied into the reference resolve here. Bounded to
    the same 8 MiB tail the map reads; anything older simply does not resolve.
    """
    if not wanted:
        return {}
    path = Path(life_dir) / "events.jsonl"
    try:
        if not path.is_file():
            return {}
    except OSError:
        return {}
    try:
        from .map_view import EVENT_PREFIXES, digest
    except Exception:  # noqa: BLE001 — fall back to explicit event ids only
        EVENT_PREFIXES, digest = (), None
    found: dict[str, str] = {}
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - _EVENT_TAIL_BYTES)
            handle.seek(start)
            data = handle.read(_EVENT_TAIL_BYTES)
    except OSError:
        return {}
    lines = data.splitlines()
    if start and lines:
        lines = lines[1:]  # drop the partial first line of a mid-file seek
    # Scan every line and keep the NEWEST text per id — the map's own dedup
    # keeps the latest revision, and quoting an outdated one would mislead.
    for raw in lines:
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        row_id = str(row.get("event_id") or "")
        if not row_id and digest is not None:
            kind = str(row.get("type") or "")
            if not kind.startswith(tuple(EVENT_PREFIXES)):
                continue
            try:
                row_id = digest(row)
            except Exception:  # noqa: BLE001 — one odd row must not stop the scan
                continue
        if row_id not in wanted:
            continue
        text = str(
            row.get("summary")
            or row.get("reason")
            or row.get("text")
            or row.get("last_message")
            or row.get("message")
            or ""
        ).strip()
        if text:
            found[row_id] = _clip(text, EVENT_TEXT_CLIP)
    return found


def _inline_line(ref: CardReference, by_id: dict[str, Any]) -> str:
    item = by_id.get(ref.task_id)
    title = _clip(
        str(getattr(item, "title", "") or "") or ref.task_title, TITLE_CLIP
    )
    step = _clip(ref.step_title, TITLE_CLIP)
    if ref.lang == "en":
        if step:
            return f'(Referenced: "{title}" · {step})'
        return f'(Referenced: "{title}")'
    if step:
        return f"（引用：《{title}》· {step}）"
    return f"（引用：《{title}》）"


def _context_entry(
    ref: CardReference,
    item: Any,
    event_texts: dict[str, str],
) -> str:
    objective = _clip(item.objective or item.original_objective, OBJECTIVE_CLIP)
    title = _clip(item.title, TITLE_CLIP)
    if ref.lang == "en":
        lines = [
            f'- Task {item.id} "{title}" (status {item.status}) — '
            f"objective: {objective}"
        ]
        if ref.step_title:
            lines.append(f"  Referenced step: {_clip(ref.step_title, TITLE_CLIP)}")
    else:
        lines = [f"- 任务 {item.id}《{title}》(状态 {item.status})——目标: {objective}"]
        if ref.step_title:
            lines.append(f"  引用环节: {_clip(ref.step_title, TITLE_CLIP)}")
    excerpts = [
        event_texts[event_id]
        for event_id in ref.event_ids[:MAX_EVENTS_PER_REFERENCE]
        if event_id in event_texts
    ]
    if excerpts:
        lines.append("  Related records:" if ref.lang == "en" else "  相关记录:")
        lines.extend(f"  - {excerpt}" for excerpt in excerpts)
    if str(item.status) in _UNSATISFIABLE_STATUSES:
        lines.append(
            f'  Note: this task already ended as "{item.status}"; '
            "new work will not wait for it."
            if ref.lang == "en"
            else f"  说明: 该任务已以「{item.status}」结束，新工作不会等待它。"
        )
    return "\n".join(lines)


def expand_operator_references(text: str, life_dir: Path | str) -> ReferenceExpansion:
    """Expand every ``[[Argus引用 {...}]]`` line in *text*.

    Returns the visible text (markers replaced with readable inline lines),
    the bounded context block for the model-facing path, and the dependency
    split for task creation. Never raises for missing tasks/events: an
    unresolvable reference keeps only its inline line.
    """
    original = str(text or "")
    if REFERENCE_MARKER_PREFIX not in original:
        return ReferenceExpansion(text=original)

    life_dir = Path(life_dir)
    by_id: dict[str, Any] | None = None
    parsed: list[CardReference] = []
    out_lines: list[str] = []
    for line in original.split("\n"):
        ref = parse_card_reference(line)
        if ref is None:
            out_lines.append(line)
            continue
        if by_id is None:
            by_id = _backlog_by_id(life_dir)
        parsed.append(ref)
        out_lines.append(_inline_line(ref, by_id))
    if not parsed:
        return ReferenceExpansion(text=original)
    assert by_id is not None

    # Dedupe identical quotes, then keep the first few for expansion. Every
    # marker line was already replaced above regardless of this cap.
    unique: list[CardReference] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for ref in parsed:
        key = (ref.task_id, ref.step_id or ref.step_title, ref.event_ids)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    capped = unique[:MAX_EXPANDED_REFERENCES]

    wanted_events: set[str] = set()
    for ref in capped:
        if ref.task_id in by_id:
            wanted_events.update(ref.event_ids[:MAX_EVENTS_PER_REFERENCE])
    event_texts = _event_texts_by_id(life_dir, wanted_events)

    # The header follows the first quote's locale; a mixed message keeps each
    # entry in the locale it was quoted under.
    header = CONTEXT_HEADER_EN if parsed[0].lang == "en" else CONTEXT_HEADER
    entries: list[str] = []
    entry_tasks: set[str] = set()
    used = len(header) + 1
    for ref in capped:
        item = by_id.get(ref.task_id)
        if item is None or ref.task_id in entry_tasks:
            continue
        entry = _context_entry(ref, item, event_texts)
        if used + len(entry) + 1 > CONTEXT_BLOCK_MAX_CHARS:
            break
        entries.append(entry)
        entry_tasks.add(ref.task_id)
        used += len(entry) + 1
    context_block = "\n".join([header, *entries]) if entries else ""

    dep_task_ids: list[str] = []
    skipped: list[str] = []
    for ref in capped:
        item = by_id.get(ref.task_id)
        if item is None or ref.task_id in dep_task_ids or ref.task_id in skipped:
            continue
        if str(item.status) in _UNSATISFIABLE_STATUSES:
            skipped.append(ref.task_id)
        else:
            dep_task_ids.append(ref.task_id)

    visible = "\n".join(out_lines)
    return ReferenceExpansion(
        text=visible,
        context_block=context_block,
        references=capped,
        dep_task_ids=dep_task_ids,
        skipped_dep_task_ids=skipped,
        matched=visible != original,
    )


__all__ = [
    "CardReference",
    "CONTEXT_BLOCK_MAX_CHARS",
    "CONTEXT_HEADER",
    "CONTEXT_HEADER_EN",
    "MAX_EXPANDED_REFERENCES",
    "REFERENCE_MARKER_PREFIX",
    "ReferenceExpansion",
    "expand_operator_references",
    "parse_card_reference",
]
