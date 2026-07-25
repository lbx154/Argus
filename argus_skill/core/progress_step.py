"""Turn one ``engineer.progress`` event into a live, operator-facing step.

The cockpit's live status line used to collapse every observable action into a
handful of euphemisms ("checking project state", "using a tool"). That told the
operator *that* something was happening but never *what*, which is exactly the
"the CLI feels frozen / I can't see what it is doing" complaint.

This module is a dumb, domain-agnostic formatter: it reports the action the
agent actually took (the command it ran, the tool it called, the files it
touched) verbatim-but-trimmed, after routing the text through
:mod:`argus_skill.core.secret_guard`. It makes no judgment about whether the
step was useful, on-track, or complete — that stays with the agent.

``describe_progress_step`` returns ``(label, detail)``:

* ``label`` — one short scannable line for the live status row.
* ``detail`` — the longer redacted body for an expandable/secondary row; may be
  empty when the label already says everything.
"""

from __future__ import annotations

from typing import Any

from .secret_guard import redact_secrets_text

# Kinds that are a reply block rather than an observable action. The Manager
# front door streams these as reply deltas, so they never become steps.
REPLY_KINDS = frozenset({"assistant_message", "agent_message", "message"})

_LABEL_LIMIT = 96
_DETAIL_LIMIT = 240

_SHELL_PREFIXES = (
    "/bin/bash -lc ",
    "/bin/bash -c ",
    "/bin/sh -lc ",
    "/bin/sh -c ",
    "bash -lc ",
    "bash -c ",
    "sh -lc ",
    "sh -c ",
)


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return (text or "").strip()


def _clip(text: str, limit: int) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(1, limit - 1)].rstrip() + "…"


def strip_shell_wrapper(command: str) -> str:
    """Unwrap ``/bin/bash -lc '<cmd>'`` so the operator sees ``<cmd>``."""
    text = (command or "").strip()
    for prefix in _SHELL_PREFIXES:
        if text.startswith(prefix):
            inner = text[len(prefix):].strip()
            if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in ("'", '"'):
                inner = inner[1:-1]
            return inner.strip()
    return text


def _tool_label(text: str) -> tuple[str, str]:
    """Split a ``"toolName: {json args}"`` progress text into label/detail."""
    body = (text or "").strip()
    name, separator, args = body.partition(":")
    if separator and name.strip() and " " not in name.strip():
        return name.strip(), args.strip()
    return _first_line(body), ""


def describe_progress_step(event: Any) -> tuple[str, str]:
    """Return ``(label, detail)`` describing one observable agent action.

    Never raises: a malformed event degrades to a generic-but-honest label
    rather than breaking the turn that produced it.
    """
    try:
        if not isinstance(event, dict):
            return "working", ""
        kind = str(event.get("kind") or "").strip()
        raw_text = redact_secrets_text(str(event.get("text") or ""))
        summary = " ".join(str(event.get("action_summary") or "").split())

        if kind == "command_execution":
            command = strip_shell_wrapper(raw_text).strip()
            head = strip_shell_wrapper(_first_line(raw_text))
            status = str(event.get("status") or "").strip().lower()
            marker = "✗ $" if status in {"failed", "error"} else "$"
            if head:
                label = f"{marker} {_clip(head, _LABEL_LIMIT - 2)}"
                # Only carry a detail when it actually adds something (a
                # multi-line or clipped command); never echo the label back.
                detail = _clip(command, _DETAIL_LIMIT)
                return label, "" if detail == _clip(head, _DETAIL_LIMIT) else detail
            return summary or "running a command", ""

        if kind in {"tool_use", "tool_call"}:
            name, args = _tool_label(raw_text)
            if name:
                label = _clip(f"⚙ {name}" + (f" · {_clip(args, 48)}" if args else ""), _LABEL_LIMIT)
                detail = _clip(args, _DETAIL_LIMIT)
                return label, "" if not detail or detail in label else detail
            return summary or "using a tool", ""

        if kind == "file_change":
            changed = event.get("changes")
            if isinstance(changed, list) and changed:
                names = [str(item) for item in changed if str(item).strip()]
                if names:
                    head = ", ".join(names[:3])
                    extra = f" +{len(names) - 3}" if len(names) > 3 else ""
                    return _clip(f"✎ {head}{extra}", _LABEL_LIMIT), ""
            first = _first_line(raw_text)
            if first:
                return _clip(f"✎ {first}", _LABEL_LIMIT), _clip(raw_text, _DETAIL_LIMIT)
            return summary or "editing files", ""

        if kind == "reasoning":
            first = _first_line(raw_text)
            return (
                _clip(f"… {first}", _LABEL_LIMIT) if first else "reasoning about the next step"
            ), ""

        if kind == "tool_result":
            first = _first_line(raw_text)
            return (_clip(f"↳ {first}", _LABEL_LIMIT) if first else "reading a tool result"), ""

        if kind in REPLY_KINDS:
            return summary or "writing the reply", ""

        first = _first_line(raw_text)
        if first:
            return _clip(first, _LABEL_LIMIT), ""
        return summary or (kind.replace("_", " ") if kind else "working"), ""
    except Exception:  # noqa: BLE001 — a status label must never break a turn
        return "working", ""


__all__ = ["REPLY_KINDS", "describe_progress_step", "strip_shell_wrapper"]
