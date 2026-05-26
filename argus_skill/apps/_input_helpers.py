"""Shared input helpers for argus-skill terminal apps (go, chat, up).

The kernel's line discipline only delivers the first ``\\n``-terminated
line to ``input()``; the rest of a multi-line paste sits in stdin's
buffer and would otherwise be silently dispatched as N follow-up
commands. This module provides a small drain helper plus a "read one
logical message (which may span pasted lines)" entry point that all
argus-skill REPLs share.
"""
from __future__ import annotations

import os
import re
import select
import sys

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z~]")
# Bracketed-paste markers some terminals inject around a paste burst.
_BRACKETED_START = "\x1b[200~"
_BRACKETED_END = "\x1b[201~"


def enable_bracketed_paste() -> None:
    """Make ``input()`` return multi-line pastes as a single string.

    Without this, GNU readline reads paste bytes greedily into its
    internal buffer and returns only the first line; the remainder
    never reaches the raw fd, so ``drain_pasted_lines`` can't recover
    it. With bracketed-paste enabled, the terminal wraps the paste in
    ``\\e[200~…\\e[201~`` markers and readline accumulates the whole
    burst into the line buffer, returning it (with embedded ``\\n``)
    on the first Enter.

    Safe to call multiple times. No-op if readline isn't available.
    """
    try:
        import readline
    except ImportError:
        return
    try:
        readline.parse_and_bind("set enable-bracketed-paste on")
    except Exception:  # noqa: BLE001 — readline is best-effort
        pass


def drain_pasted_lines(timeout: float = 0.10, *, max_bytes: int = 65536) -> list[str]:
    """Read any extra lines already sitting in stdin's buffer right after
    ``input()`` returned. Returns the post-first-line lines (still raw —
    callers decide whether to strip blanks). ANSI / bracketed-paste
    markers are stripped.

    Newlines are preserved as element boundaries (one element per line)
    so callers can re-join with their preferred separator (``\\n`` for
    REPL messages that should preserve multi-line structure, ``" "`` for
    objective-style prompts that want a single sentence).
    """
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, OSError):
        return []

    chunks: list[bytes] = []
    total = 0
    poll = timeout
    while total < max_bytes:
        try:
            ready, _, _ = select.select([fd], [], [], poll)
        except (ValueError, OSError):
            break
        if not ready:
            break
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        # After the first follow-on chunk arrives, give a much shorter
        # grace period for the remainder of the same paste burst.
        poll = 0.04

    if not chunks:
        return []
    text = b"".join(chunks)[:max_bytes].decode("utf-8", errors="replace")
    # Strip terminal control noise.
    text = text.replace(_BRACKETED_START, "").replace(_BRACKETED_END, "")
    text = _ANSI_RE.sub("", text)
    return text.splitlines()


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def read_pasted_message(prompt: str = "> ") -> str | None:
    """Read one logical message from stdin, preserving paste newlines.

    Returns ``None`` on EOF / Ctrl-D so callers can break their loop.
    Returns ``""`` if the user pressed Enter on an empty line (callers
    typically treat that as "ignore"). Pasted multi-line content is
    joined with ``\\n`` so code snippets, JSON blobs, etc. retain their
    structure when forwarded to the daemon / engineer.

    When stdin is *not* a TTY (e.g. heredoc / piped script), select()-based
    paste drain is unreliable because pipe buffering delivers lines in
    bursts that don't align with terminal paste markers. In that mode we
    instead aggregate consecutive non-empty lines into one logical message,
    using a blank line (or a line starting with ``/``) as the boundary.
    A leading ``/`` line is returned by itself so REPL slash-commands
    still parse correctly when scripted.
    """
    sys.stdout.write(prompt)
    sys.stdout.flush()

    if not _stdin_is_tty():
        return _read_piped_block()

    try:
        first = input()
    except EOFError:
        return None
    extras = drain_pasted_lines()
    if not extras:
        return first
    parts = [first, *extras]
    while parts and parts[-1] == "":
        parts.pop()
    return "\n".join(parts)


_piped_pushback: list[str] = []


def _read_piped_block() -> str | None:
    """Read one logical message from a non-TTY stdin.

    Boundaries: blank line, EOF, or a slash-command line (which is
    returned as a standalone message and the rest pushed back).
    """
    global _piped_pushback
    buffer: list[str] = []

    def _read_bracketed_block(first_line: str) -> str:
        text = first_line
        while _BRACKETED_END not in text:
            try:
                text += "\n" + input()
            except EOFError:
                break

        start = text.find(_BRACKETED_START)
        if start >= 0:
            text = text[start + len(_BRACKETED_START):]

        end = text.find(_BRACKETED_END)
        if end >= 0:
            body = text[:end]
            rest = text[end + len(_BRACKETED_END):]
            pushback = [line for line in rest.splitlines() if line.strip()]
            if pushback:
                _piped_pushback[:0] = pushback
        else:
            body = text

        return _ANSI_RE.sub("", body)

    def _flush() -> str | None:
        while buffer and buffer[-1] == "":
            buffer.pop()
        if not buffer:
            return ""
        return "\n".join(buffer)

    while True:
        if _piped_pushback:
            line = _piped_pushback.pop(0)
        else:
            try:
                line = input()
            except EOFError:
                if buffer:
                    return _flush()
                return None
        if _BRACKETED_START in line:
            if buffer:
                _piped_pushback.insert(0, line)
                return _flush()
            return _read_bracketed_block(line)
        stripped = line.strip()
        if stripped == "":
            if buffer:
                return _flush()
            continue
        if stripped.startswith("/"):
            if buffer:
                # End current block; defer the slash-command to the next call.
                _piped_pushback.insert(0, line)
                return _flush()
            return line
        buffer.append(line)
