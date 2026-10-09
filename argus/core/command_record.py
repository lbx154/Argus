"""What the host saw of a role's commands, kept in host memory for one turn.

A Reviewer whose tools only read and search cannot rerun a check. What it can
weigh instead is what the host itself observed: that a command ran, and the
exit code the agent CLI reported for it. The command text and its output are
still the Engineer's (its command printed them), so they are evidence to read,
not facts to trust.

The record lives in memory, not in a file. A file the Engineer can write could
plant a second log or append a forged result; a capture fed only by the agent
CLI's own stream cannot be written by any role. Within a capture, the first
result recorded for a call is final: a later report for the same call id never
replaces it.

Nothing here decides anything. :mod:`argus.adapters.stream_progress` feeds the
active captures; the Engineer round opens one around its turn and hands the
runs to the round-evidence providers, which render them for the Reviewer.
"""
from __future__ import annotations

import itertools
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace

#: Backends whose stream reports each shell command's exit code, so the host
#: can record a result for it. Others report a command's start (and at most
#: its failure); a Reviewer must not be told to rely on results that cannot
#: exist there.
RESULT_RECORDING_BACKENDS = frozenset({"copilot", "codex"})

_SHELL_TRAILER = re.compile(r"^<shellId: .*>$")
_KEY_LINE = re.compile(
    r"(?i)\b(?:fail(?:ed|s|ure|ures)?|errors?|traceback|exception|"
    r"\w*error|passed|skipped|deselected|xfail(?:ed)?|xpass(?:ed)?|"
    r"mismatch(?:es)?|warnings?)\b|^(?:ok|failed)\b|^ran \d+ tests?"
)
_LINE_CHARS = 200
_KEY_LINES = 4
_TAIL_LINES = 3
_DIGEST_CHARS = 800
_MAX_RUNS = 2_000


def backend_records_command_results(backend: object) -> bool:
    """Whether a runner of this backend reports each command's exit code."""
    name = str(getattr(backend, "backend", backend) or "").strip().lower()
    return name in RESULT_RECORDING_BACKENDS


def output_digest(raw: object) -> str:
    """Failure, error and summary lines from a whole output, then its last lines.

    A verdict is usually at the end of an output, but a failure can sit above a
    long tail (a traceback before a summary, a failing case before cleanup
    chatter), so the digest keeps both.
    """
    text = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [line for line in lines if not _SHELL_TRAILER.match(line)]
    if not lines:
        return ""
    tail = lines[-_TAIL_LINES:]
    body = lines[:-_TAIL_LINES]
    keys = [line for line in body if _KEY_LINE.search(line)]
    if len(keys) > _KEY_LINES:
        half = _KEY_LINES // 2
        keys = keys[:half] + keys[-(_KEY_LINES - half):]
    keys = list(dict.fromkeys(keys))
    parts = [_clip(line) for line in keys]
    if keys and len(body) > len(keys):
        parts.append("…")
    parts.extend(_clip(line) for line in tail)
    digest = " | ".join(parts)
    return digest if len(digest) <= _DIGEST_CHARS else "…" + digest[-(_DIGEST_CHARS - 1):].lstrip()


def _redact(text: str) -> str:
    from .secret_guard import known_secret_values, redact_secrets_text

    return redact_secrets_text(text, known_values=known_secret_values()) if text else ""


def _clip(line: str) -> str:
    return line if len(line) <= _LINE_CHARS else line[: _LINE_CHARS - 1] + "…"


@dataclass(frozen=True)
class CommandRun:
    """One tool call of a role as the host saw it.

    ``kind`` is ``"command"`` for a shell command and ``"tool"`` for any other
    tool. ``exit_code`` is ``None`` until the CLI reports a result, and for a
    failure reported without one (``failed`` then says so).
    """

    call_id: str
    kind: str
    text: str
    started_at: float
    tool: str = ""
    finished: bool = False
    exit_code: int | None = None
    failed: bool = False
    output: str = ""


@dataclass
class CommandCapture:
    """The calls of one role turn, in the order they started."""

    label: str
    started_at: float = field(default_factory=time.time)
    _runs: dict[str, CommandRun] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def start(self, call_id: str, kind: str, text: str, *, tool: str = "", ts: float | None = None) -> None:
        with self._lock:
            if call_id in self._runs or len(self._runs) >= _MAX_RUNS:
                return
            self._runs[call_id] = CommandRun(
                call_id=call_id, kind=kind, text=_redact(text), tool=tool,
                started_at=time.time() if ts is None else ts,
            )

    def finish(
        self, call_id: str, *, exit_code: int | None, failed: bool, output: str,
        kind: str = "command", text: str = "", tool: str = "", ts: float | None = None,
    ) -> None:
        """Record a call's result once; a later result for the same call is ignored."""
        with self._lock:
            run = self._runs.get(call_id)
            if run is None:
                if len(self._runs) >= _MAX_RUNS:
                    return
                run = CommandRun(
                    call_id=call_id, kind=kind, text=_redact(text), tool=tool,
                    started_at=time.time() if ts is None else ts,
                )
            elif run.finished:
                return
            self._runs[call_id] = replace(
                run, finished=True, exit_code=exit_code,
                failed=failed or (exit_code is not None and exit_code != 0),
                output=_redact(output_digest(output)),
            )

    def runs(self) -> list[CommandRun]:
        with self._lock:
            return list(self._runs.values())


_ACTIVE: dict[str, list[CommandCapture]] = {}
_ACTIVE_LOCK = threading.Lock()
_ANONYMOUS = itertools.count(1)


def anonymous_call_id() -> str:
    """A call id that matches no other call, for a dialect that reports none."""
    return f"anonymous-{next(_ANONYMOUS)}"


@contextmanager
def capture(label: str) -> Iterator[CommandCapture]:
    """Record the calls the stream reports for ``label`` while the block runs.

    A helper turn of the same role (``engineer-r2.winddown``) belongs to it.
    """
    record = CommandCapture(label=label)
    with _ACTIVE_LOCK:
        _ACTIVE.setdefault(label, []).append(record)
    try:
        yield record
    finally:
        with _ACTIVE_LOCK:
            records = _ACTIVE.get(label, [])
            if record in records:
                records.remove(record)
            if not records:
                _ACTIVE.pop(label, None)


def captures_for(actor: str) -> list[CommandCapture]:
    """The open captures a stream line from ``actor`` belongs to."""
    if not actor:
        return []
    with _ACTIVE_LOCK:
        if not _ACTIVE:
            return []
        found: list[CommandCapture] = []
        for label, records in _ACTIVE.items():
            if actor == label or actor.startswith(label + "."):
                found.extend(records)
        return found


__all__ = [
    "RESULT_RECORDING_BACKENDS",
    "CommandCapture",
    "CommandRun",
    "anonymous_call_id",
    "backend_records_command_results",
    "capture",
    "captures_for",
    "output_digest",
]
