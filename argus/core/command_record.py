"""What the agent CLI reported of a role's commands, kept in host memory for one turn.

A Reviewer whose tools only read and search cannot rerun a check. What it can
weigh instead is what the host recorded from the agent CLI's output stream:
which commands the CLI reported starting and the exit codes it reported for
them. The command text and its output are still the Engineer's (its command
printed them), so they are evidence to read, not facts to trust.

The record is not tamper-proof. A command can write CLI-shaped lines to the
stream the host reads, so a reported start or result may not be real. The
record therefore never quietly settles a disagreement: a second, different
result for a call is kept beside the first as a conflict, and a result with no
reported start, or naming a different command than its start, is marked
unverified. The round log shows both. Keeping the record in memory rather
than in a project file only removes the easiest forgeries: a planted log file
or an appended line.

Nothing here decides anything. :mod:`argus.adapters.stream_progress` feeds the
active captures; the Engineer round opens one around its turn and hands the
runs to the round-evidence providers, which render them for the Reviewer.
"""
from __future__ import annotations

import itertools
import re
import threading
import time
from collections import OrderedDict
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
_MAX_RESULTS = 4
_TEXT_CHARS = 4_000


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
class CommandResult:
    """One result the CLI reported for a call."""

    exit_code: int | None
    failed: bool
    output: str = ""


@dataclass(frozen=True)
class CommandRun:
    """One tool call of a role as the CLI reported it.

    ``kind`` is ``"command"`` for a shell command and ``"tool"`` for any other
    tool. ``results`` holds every distinct result reported for the call, in
    arrival order; more than one is a conflict. ``unverified`` says why the
    call cannot be tied to a reported start, or is empty.
    """

    call_id: str
    kind: str
    text: str
    started_at: float
    tool: str = ""
    results: tuple[CommandResult, ...] = ()
    unverified: str = ""

    @property
    def finished(self) -> bool:
        return bool(self.results)

    @property
    def conflicting(self) -> bool:
        return len(self.results) > 1

    @property
    def exit_code(self) -> int | None:
        return self.results[0].exit_code if self.results else None

    @property
    def failed(self) -> bool:
        return any(result.failed for result in self.results)

    @property
    def output(self) -> str:
        return self.results[0].output if self.results else ""


def _flat(text: str) -> str:
    return " ".join(str(text or "").split())


def _clip_text(text: str) -> str:
    text = _redact(text)
    if len(text) <= _TEXT_CHARS:
        return text
    return text[: _TEXT_CHARS - 1000] + " … " + text[-900:]


@dataclass
class CommandCapture:
    """The calls of one role turn, in the order they started.

    At most ``_MAX_RUNS`` calls are kept; the oldest give way to new ones and
    ``dropped`` counts them.
    """

    label: str
    started_at: float = field(default_factory=time.time)
    dropped: int = 0
    _runs: OrderedDict[str, CommandRun] = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _add(self, run: CommandRun) -> None:
        while len(self._runs) >= _MAX_RUNS:
            self._runs.popitem(last=False)
            self.dropped += 1
        self._runs[run.call_id] = run

    def start(self, call_id: str, kind: str, text: str, *, tool: str = "", ts: float | None = None) -> None:
        with self._lock:
            run = self._runs.get(call_id)
            if run is None:
                self._add(CommandRun(
                    call_id=call_id, kind=kind, text=_clip_text(text), tool=tool,
                    started_at=time.time() if ts is None else ts,
                ))
            elif _flat(_clip_text(text)) != _flat(run.text) and not run.unverified:
                self._runs[call_id] = replace(
                    run, unverified="a second start named a different command: "
                    + _flat(_clip_text(text))[:200],
                )

    def finish(
        self, call_id: str, *, exit_code: int | None, failed: bool, output: str,
        kind: str = "command", text: str = "", tool: str = "", ts: float | None = None,
    ) -> None:
        """Record a result; a different later result is kept beside the first as a conflict."""
        result = CommandResult(
            exit_code=exit_code,
            failed=failed or (exit_code is not None and exit_code != 0),
            output=_redact(output_digest(output)),
        )
        with self._lock:
            run = self._runs.get(call_id)
            if run is None:
                self._add(CommandRun(
                    call_id=call_id, kind=kind, text=_clip_text(text) or "(command not reported)",
                    tool=tool, started_at=time.time() if ts is None else ts, results=(result,),
                    unverified="no start was reported for this call",
                ))
                return
            unverified = run.unverified
            if text and run.text and _flat(_clip_text(text)) != _flat(run.text) and not unverified:
                unverified = "the result names a different command than its start"
            results = run.results
            if result not in results and len(results) < _MAX_RESULTS:
                results = (*results, result)
            self._runs[call_id] = replace(run, results=results, unverified=unverified)

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

    Lines from ``label`` itself and from its dotted sub-labels count, but only
    while the block is open; a later helper turn is not part of the record.
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
    "CommandResult",
    "CommandRun",
    "anonymous_call_id",
    "backend_records_command_results",
    "capture",
    "captures_for",
    "output_digest",
]
