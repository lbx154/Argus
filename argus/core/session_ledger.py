"""What a provider session already holds, so a resumed turn sends only what is new.

A role's fixed text (its instructions, Skill libraries, vertical and stage
rules, standing project context) goes once, at the start of the provider
session. A resumed thread keeps that text in its history, and the provider
re-sends the whole history with every request, so a second copy is paid on
every later request of the session. Later turns therefore send the task and
the new inputs: a section byte-identical to one the thread already received
is named instead of repeated, and a changed section is sent again (the new
version is the change). On 2026-10-26 one ACL project re-sent about 58 KB of
unchanged sections into resumed Planner, Manager and Engineer threads.

The ledger also keeps a rough size of what each thread holds, so a role can
start a fresh session with its full prompt instead of resuming a long history
that is mostly stale tool output (:func:`session_size_tokens`).

Every request goes through :mod:`argus.core.run_gateway`, which is the only
writer. Ledger I/O never decides a turn: unreadable state means "nothing
recorded", which sends the full prompt.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable

from .atomic_replace import write_atomic

log = logging.getLogger(__name__)

_SECTION_HEADING = re.compile(r"^#{2,3} \S")
# Sections shorter than this stay inline: naming them costs about as much.
_ELIDE_MIN_SECTION_CHARS = 400
_NOTE_HEADING = "## Unchanged since your previous turn"
_NOTE_TITLES = re.compile(r"`([^`]+)`")
_MAX_DIGESTS = 1024
# Codex keeps at most about 10k tokens of one tool output in the thread.
_MAX_ITEM_BYTES = 40_000
# Mixed English/Chinese text: about 3 UTF-8 bytes per token.
_BYTES_PER_TOKEN = 3


def prompt_sections(prompt: str) -> list[str]:
    """Split a prompt at its level-2/3 headings, ignoring fenced code."""
    sections: list[str] = []
    current: list[str] = []
    fenced = False
    for line in str(prompt).splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and current and _SECTION_HEADING.match(line):
            sections.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        sections.append("\n".join(current))
    return sections


def section_digest(section: str) -> str:
    return hashlib.sha256(section.strip().encode("utf-8")).hexdigest()[:32]


def _note_titles(section: str) -> list[str]:
    body = section.strip()
    if not body.startswith(_NOTE_HEADING):
        return []
    return _NOTE_TITLES.findall(body)


def elide_known_sections(prompt: str, known: Iterable[str]) -> str:
    """Drop the sections whose digest is in ``known``; name them in one note.

    The first section frames the turn and is always kept, as are short
    sections and sections without a level-2/3 heading. A note an earlier step
    already wrote (the Planner's own previous-turn elision) is merged into the
    one note this writes.
    """
    known = set(known)
    sections = prompt_sections(prompt)
    kept: list[str] = []
    omitted: list[str] = []
    prior_titles: list[str] = []
    for index, section in enumerate(sections):
        body = section.strip()
        titles = _note_titles(section) if index else []
        if titles:
            prior_titles.extend(titles)
            continue
        if (
            index
            and len(body) >= _ELIDE_MIN_SECTION_CHARS
            and _SECTION_HEADING.match(body)
            and section_digest(body) in known
        ):
            omitted.append(body.splitlines()[0].lstrip("#").strip())
        else:
            kept.append(section)
    if not omitted:
        return prompt
    titles = list(dict.fromkeys([*prior_titles, *omitted]))
    note = (
        f"{_NOTE_HEADING}\n"
        "These sections are identical to the copies already in this session and "
        "still apply as written: "
        + "; ".join(f"`{title}`" for title in titles)
        + ".\n"
    )
    return "\n".join([kept[0], note, *kept[1:]]) + "\n"


def _ledger_dir() -> Path:
    from .paths import global_root

    return global_root() / "session-ledger"


def _ledger_path(thread_id: str) -> Path:
    name = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:40]
    return _ledger_dir() / f"{name}.json"


def _load(thread_id: str) -> dict[str, Any]:
    if not thread_id:
        return {}
    try:
        payload = json.loads(_ledger_path(thread_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("thread_id") != thread_id:
        return {}
    return payload


def _save(thread_id: str, payload: dict[str, Any]) -> None:
    path = _ledger_path(thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(payload), mode=0o600)


def elide_sent_sections(thread_id: str | None, prompt: str) -> str:
    """The prompt to send on a resumed ``thread_id``: only what it lacks."""
    if not thread_id:
        return prompt
    known = _load(thread_id).get("sections")
    if not isinstance(known, list) or not known:
        return prompt
    return elide_known_sections(prompt, (str(item) for item in known))


def session_size_tokens(thread_id: str | None) -> int:
    """Rough tokens of Argus prompts and turn output this thread holds; 0 if unknown."""
    try:
        return max(0, int(_load(thread_id or "").get("tokens") or 0))
    except (TypeError, ValueError):
        return 0


def _item_bytes(item: dict[str, Any]) -> int:
    total = 0
    for key in ("command", "text", "message"):
        value = item.get(key)
        if isinstance(value, str):
            total += len(value.encode("utf-8"))
    output = item.get("aggregated_output")
    if isinstance(output, str):
        total += min(len(output.encode("utf-8")), _MAX_ITEM_BYTES)
    for key in ("arguments", "result", "changes"):
        if item.get(key) is not None:
            total += min(len(json.dumps(item[key], ensure_ascii=False).encode("utf-8")), _MAX_ITEM_BYTES)
    return total


def turn_size_tokens(prompt: str, stdout_lines: Iterable[str] | None) -> int:
    """Rough tokens one turn adds to its thread: the prompt sent plus its items.

    Codex ``exec --json`` items are measured by their text; any other JSON
    line counts at half its length (escaping), capped like a tool output.
    """
    size = len(str(prompt).encode("utf-8"))
    for line in stdout_lines or ():
        text = str(line or "")
        if not text.startswith("{"):
            continue
        try:
            event = json.loads(text)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "item.completed" and isinstance(event.get("item"), dict):
            size += _item_bytes(event["item"])
        elif not str(event.get("type") or "").startswith(("item.", "turn.", "thread.")):
            size += min(len(text), _MAX_ITEM_BYTES) // 2
    return size // _BYTES_PER_TOKEN


def _prune(max_age_seconds: float = 30 * 86_400) -> None:
    """Forget threads untouched for a month; a stale thread is resumed rarely."""
    cutoff = time.time() - max_age_seconds
    try:
        with os.scandir(_ledger_dir()) as entries:
            for entry in entries:
                if entry.name.endswith(".json") and entry.stat().st_mtime < cutoff:
                    Path(entry.path).unlink(missing_ok=True)
    except OSError:
        pass


def record_sent(
    thread_id: str | None,
    prompt: str,
    *,
    holds_prompt: bool,
    added_tokens: int,
) -> None:
    """Note what one finished turn left in ``thread_id``.

    ``holds_prompt`` is true when the turn completed, so the thread now holds
    every section of ``prompt`` (the full prompt, before elision: the elided
    sections were already there).
    """
    if not thread_id:
        return
    try:
        payload = _load(thread_id)
        if not payload:
            _prune()
        sections = [str(item) for item in payload.get("sections") or [] if item]
        if holds_prompt:
            seen = set(sections)
            for section in prompt_sections(prompt):
                digest = section_digest(section)
                if digest not in seen:
                    seen.add(digest)
                    sections.append(digest)
        _save(thread_id, {
            **payload,
            "thread_id": thread_id,
            "sections": sections[-_MAX_DIGESTS:],
            "tokens": max(0, int(payload.get("tokens") or 0)) + max(0, int(added_tokens)),
            "updated_at": time.time(),
        })
    except Exception:  # noqa: BLE001 - the ledger is an optimization, never a requirement
        log.debug("session ledger write failed for %s", thread_id, exc_info=True)


def usage_totals(thread_id: str | None) -> tuple[int, ...] | None:
    """The provider's last cumulative usage totals for ``thread_id``, if recorded.

    Codex reports usage cumulatively over a thread's life. A process that
    resumes a thread another process started has no baseline in memory, and
    without this one charged the whole history again (2026-10-26: a Manager
    turn of 730k tokens was recorded as 1.46M and $1.25).
    """
    raw = _load(thread_id or "").get("usage_totals")
    if not isinstance(raw, list) or not raw:
        return None
    try:
        return tuple(max(0, int(value)) for value in raw)
    except (TypeError, ValueError):
        return None


def remember_usage_totals(thread_id: str | None, totals: tuple[int, ...]) -> None:
    if not thread_id:
        return
    try:
        payload = _load(thread_id)
        if not payload:
            _prune()
        _save(thread_id, {
            **payload,
            "thread_id": thread_id,
            "usage_totals": [max(0, int(value)) for value in totals],
            "updated_at": time.time(),
        })
    except Exception:  # noqa: BLE001 - accounting metadata never fails a turn
        log.debug("session ledger usage write failed for %s", thread_id, exc_info=True)


__all__ = [
    "remember_usage_totals",
    "usage_totals",
    "elide_known_sections",
    "elide_sent_sections",
    "prompt_sections",
    "record_sent",
    "section_digest",
    "session_size_tokens",
    "turn_size_tokens",
]
