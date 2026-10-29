"""Constants and pure helpers shared by the runner's exec/stream path.

No side effects and no dependency on the rest of the runner.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from ..core.secret_guard import redact_secrets_text

# These deques bound RAM; complete provider output is persisted in agent I/O logs.
_CAPTURE_STDOUT_LINES = 512
_CAPTURE_STDERR_LINES = 256
_CAPTURE_JSON_EVENTS = 2048
_STREAM_QUEUE_LINES = 4096
# These labels sit inside the synchronous Manager request even though they use
# older or role-specific names.
_SYNCHRONOUS_MANAGER_TURN_LABELS = frozenset(
    {
        "chat-1",
        "simple-1",
    }
)


def _is_manager_turn_label(run_label: str | None) -> bool:
    label = str(run_label or "").strip().lower()
    return (
        label.startswith(("manager-", "manager."))
        or label in _SYNCHRONOUS_MANAGER_TURN_LABELS
    )


# The last lines of a CLI's stderr are usually where the actual reason for a
# failed exit is written ("connect ECONNREFUSED 127.0.0.1:18765", "Failed to
# load models"); without them all anyone sees is the exit code. The tail is
# bounded so it stays readable inside a failure record, terminal colour and
# control sequences are dropped, credentials are redacted, and local paths are
# kept on purpose: they are what makes a crash actionable.
_STDERR_TAIL_LINES = 20
_STDERR_TAIL_CHARS = 2048
_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI: colours, cursor movement
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC: titles, hyperlinks
    r"|\x1b[@-Z\\-_]"  # two-byte escapes
)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CLI_EXITED_WITHOUT_TURN = "Agent CLI exited without completing a model turn."


def stderr_tail(stderr_lines: Iterable[str | bytes]) -> str:
    """The last lines of a CLI's stderr as clean, redacted, valid UTF-8 text.

    Empty when the CLI printed nothing worth showing. Blank lines inside the
    tail are kept so the text reads as the CLI wrote it.
    """
    lines: list[str] = []
    for raw in stderr_lines:
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        lines.append(_CONTROL_CHAR_RE.sub("", _ANSI_ESCAPE_RE.sub("", text)).rstrip())
    while lines and not lines[-1]:
        lines.pop()
    tail = "\n".join(lines[-_STDERR_TAIL_LINES:]).strip()
    if len(tail) > _STDERR_TAIL_CHARS:
        tail = tail[-_STDERR_TAIL_CHARS:]
        cut = tail.find("\n")
        if 0 <= cut < len(tail) - 1:
            tail = tail[cut + 1:]
    tail = tail.encode("utf-8", errors="replace").decode("utf-8")
    return redact_secrets_text(tail)


def _incomplete_turn_error(
    stderr_lines: Iterable[str | bytes],
    *,
    receipt: str = "",
    log_hint: str = "",
) -> str:
    """Best available diagnostic for a CLI that exited without a model turn.

    ``receipt`` is the runner's own one-line account of how the call ended
    ("Copilot CLI exited with code 1."). The CLI's last stderr lines follow it,
    because that is where the reason usually is. When the CLI printed nothing,
    the record says so and, if ``log_hint`` names the CLI's own log directory,
    where to look instead.
    """
    tail = stderr_tail(stderr_lines)
    receipt = str(receipt or "").strip()
    if receipt and not receipt.endswith((".", "!", "?")):
        receipt += "."
    if tail and receipt:
        return f"{receipt}\n{tail}"
    if tail:
        return tail
    if receipt:
        where = f"; its own log is under {log_hint}" if log_hint else ""
        return f"{receipt} It printed nothing on stderr{where}."
    return _CLI_EXITED_WITHOUT_TURN
