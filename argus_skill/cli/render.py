"""Terminal-side event rendering.

The daemon emits raw structured events to JSONL outbox; this module
turns them into pretty multi-line terminal output. We layer ANSI
color + box drawing + round dividers on top of the existing
``format_event_message`` so Telegram / JSONL-only consumers stay
plain-text.
"""

from __future__ import annotations

import os
from typing import Any

from .event_format import _trunc, format_event_message
from .theme import BOX, Theme

# ── per-event-type coloring ───────────────────────────────────────────────

_REVIEW_STATUS_COLOR = {
    "✅": "bold_green",
    "↻": "yellow",
    "⛔": "bold_red",
    "🚫": "bold_red",
}


def _colorize_first_line(line: str, color_method: str, theme: Theme) -> str:
    """Apply a colour to the first line; leave the rest untouched."""
    if "\n" in line:
        head, _, rest = line.partition("\n")
        return getattr(theme, color_method)(head) + "\n" + rest
    return getattr(theme, color_method)(line)


def _round_index_from_event(event: dict[str, Any]) -> int | None:
    idx = event.get("round_index")
    if isinstance(idx, int) and idx > 0:
        return idx
    return None


def render_event_for_terminal(
    event: dict[str, Any],
    *,
    theme: Theme,
) -> str:
    """Render one event as a (possibly multi-line) terminal string.

    Special-cases:
      * ``round.started``  prepends a horizontal rule with ``Round N``.
      * ``mission.started`` prepends a thicker rule.
      * ``status.report``  rendered in a left-bordered block.
      * ``command.ack`` with ``show_kind`` → left-bordered block.
      * ``command.ack`` plain → dim / cyan accent.
      * ``round.review.completed`` → status icon coloured by verdict.

    Falls back to ``format_event_message`` for anything else and
    applies a per-icon color.
    """
    kind = str(event.get("type", ""))

    if kind == "engineer.progress":
        return _render_engineer_progress_terminal(event, theme=theme)

    if kind == "round.started":
        round_idx = _round_index_from_event(event) or "?"
        rule = theme.hr(f"Round {round_idx}")
        return "\n" + rule

    if kind == "mission.started":
        body = format_event_message(event)
        rule_top = theme.hr("Mission")
        return "\n" + rule_top + "\n" + theme.bold_cyan(body)

    if kind == "loop.started":
        body = format_event_message(event)
        return _colorize_first_line(body, "bold_cyan", theme)

    if kind == "loop.completed":
        body = format_event_message(event)
        success = event.get("success", True)
        method = "bold_green" if success else "bold_red"
        return _colorize_first_line(body, method, theme)

    if kind == "mission.completed":
        body = format_event_message(event)
        # mission.completed text starts with "mission ID: success=True/False"
        success = "success=True" in (event.get("text") or "")
        method = "bold_green" if success else "bold_red"
        return _colorize_first_line(body, method, theme)

    if kind == "mission.error":
        return _colorize_first_line(format_event_message(event), "bold_red", theme)

    if kind == "mission.idle":
        # Render in a soft cyan callout box (left-bordered).
        body = format_event_message(event)
        # Strip leading icon; we'll show it in the title.
        return theme.left_box(
            [theme.dim(body[2:].strip()) if body.startswith("🟦 ") else theme.dim(body)],
            title=theme.bold_cyan("🟦 mission idle"),
        )

    if kind == "round.main.completed":
        body = format_event_message(event)
        return _colorize_first_line(body, "bold_blue", theme)

    if kind == "round.checks.completed":
        return _colorize_first_line(format_event_message(event), "yellow", theme)

    if kind == "round.review.completed":
        body = format_event_message(event)
        # The renderer sticks a status icon (✅ ↻ ⛔ 🚫) into the first
        # line; pick a color from the icon character.
        first = body.split("\n", 1)[0]
        method = "bold_blue"
        for icon, m in _REVIEW_STATUS_COLOR.items():
            if icon in first:
                method = m
                break
        return _colorize_first_line(body, method, theme)

    if kind == "plan.completed":
        return _colorize_first_line(format_event_message(event), "magenta", theme)

    if kind in ("final.report.ready", "pptx.report.ready"):
        return _colorize_first_line(format_event_message(event), "bold_magenta", theme)

    if kind == "command.error":
        return _colorize_first_line(format_event_message(event), "bold_red", theme)

    if kind == "command.unknown":
        return theme.yellow(format_event_message(event))

    if kind == "command.ack" and event.get("show_kind"):
        return _render_show_ack(event, theme=theme)

    if kind == "status.report":
        return _render_status_report(event, theme=theme)

    if kind == "help":
        body = format_event_message(event)
        return _colorize_first_line(body, "cyan", theme)

    if kind == "command.ack":
        return _colorize_first_line(format_event_message(event), "green", theme)

    if kind in ("distill.start", "distill.done"):
        return _colorize_first_line(format_event_message(event), "magenta", theme)

    if kind == "match.info":
        return theme.cyan(format_event_message(event))

    if kind == "daemon.stopping":
        return _colorize_first_line(format_event_message(event), "bold_red", theme)

    # Fallback: plain.
    return format_event_message(event)


# ── special-cased renderers ──────────────────────────────────────────────

def _render_status_report(event: dict[str, Any], *, theme: Theme) -> str:
    """Render the multi-line /status snapshot inside a left-bordered box."""
    body = (event.get("text") or "").rstrip("\n")
    if not body:
        return theme.dim("(no status)")

    raw_lines = body.splitlines()
    # The daemon's _render_mission_status produces:
    #   header line               (mission ID  status  round X/Y  phase=…)
    #   "   key: value" lines     (objective / plan_mode / last review / …)
    #   "   recent:"              (literal label)
    #   "     HH:MM:SS rendered…" (recent events list)
    header_raw = raw_lines[0].strip() if raw_lines else ""
    title = theme.bold_cyan("📊 " + _highlight_status_header(header_raw, theme))

    body_lines: list[str] = []
    in_recent = False
    for line in raw_lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "recent:":
            in_recent = True
            body_lines.append(theme.dim(BOX["left_mid"] + BOX["h"]) + " "
                              + theme.bold("recent"))
            continue
        if in_recent:
            # Each recent line starts with "HH:MM:SS rendered_message".
            ts, _, rest = stripped.partition(" ")
            body_lines.append(theme.gray(ts) + "  " + rest)
        else:
            # "key: value" → bold key, plain value.
            if ":" in stripped:
                key, _, val = stripped.partition(":")
                body_lines.append(theme.bold(key) + ":" + val)
            else:
                body_lines.append(stripped)

    return theme.left_box(body_lines, title=title)


def _highlight_status_header(header: str, theme: Theme) -> str:
    """Color the status header tokens.

    Examples seen:
      * ``mission abc-123   running   round 3/10   phase=engineering``
      * ``mission abc-123   done   round 2/5   phase=idle``
      * ``mission abc-123   error   ...``
    """
    parts = [p for p in header.split("   ") if p]
    out: list[str] = []
    for p in parts:
        ps = p.strip()
        low = ps.lower()
        if low in ("running",):
            out.append(theme.bold_blue(ps))
        elif low in ("done",):
            out.append(theme.bold_green(ps))
        elif low in ("error",):
            out.append(theme.bold_red(ps))
        elif ps.startswith("phase="):
            phase = ps.split("=", 1)[1]
            phase_color = {
                "ready": "cyan", "engineering": "bold_blue",
                "checks": "yellow", "review": "magenta",
                "planning": "magenta", "idle": "gray",
            }.get(phase, "cyan")
            out.append("phase=" + getattr(theme, phase_color)(phase))
        else:
            out.append(ps)
    return "  ·  ".join(out)


def _render_show_ack(event: dict[str, Any], *, theme: Theme) -> str:
    """Render /show response as a left-bordered code-style block."""
    show_kind = event.get("show_kind", "?")
    text = (event.get("text") or "").rstrip()
    if not text:
        return theme.left_box(
            [theme.dim("(empty)")],
            title=theme.bold_cyan(f"📂 /show {show_kind}"),
        )
    # Strip "── label ──\n" headers we already have from the daemon
    # and turn them into in-block sub-headers. If the body is a unified diff
    # (e.g. `/show review` of the engineer's change), colour +/- lines.
    is_diff = _looks_like_diff(text)
    lines: list[str] = []
    for raw in text.splitlines():
        if raw.startswith("── ") and raw.rstrip().endswith(" ──"):
            label = raw.strip().strip("─").strip()
            if lines:
                lines.append("")  # blank between sections
            lines.append(theme.bold_magenta(label))
        elif is_diff:
            lines.append(_colorize_diff_line(raw, theme=theme))
        else:
            lines.append(raw)
    return theme.left_box(
        lines,
        title=theme.bold_cyan(f"📂 /show {show_kind}"),
    )


# ── engineer.progress: model speech vs. operations ───────────────────────

# Empty string from the renderer means "swallow this event entirely" —
# useful for hiding `reasoning` items by default since they're inner
# monologue, not user-visible communication. Set
# ARGUS_SKILL_SHOW_REASONING=1 to opt back in.
_SHOW_REASONING = os.environ.get("ARGUS_SKILL_SHOW_REASONING", "0").lower() in (
    "1", "true", "yes", "on",
)


def _render_engineer_progress_terminal(event: dict[str, Any], *, theme: Theme) -> str:
    """Two visually-distinct lanes: the model's *speech* vs. its *operations*.

    Model speech (``assistant_message`` / ``agent_message``) is what the
    user actually wants to read — render bright, full-width, multi-line
    preserved, with a ``▌`` left bar so it reads like a quoted speaker
    turn.

    Operations (``command_execution`` / ``tool_use`` / ``file_change``)
    are bookkeeping the user only glances at — render dim, single-line,
    indented under a small ``▸`` so they recede visually.

    ``reasoning`` is the model's internal scratchpad — hidden by default
    (set ``ARGUS_SKILL_SHOW_REASONING=1`` to see it as faint italic).
    """
    kind = str(event.get("kind") or "").strip()
    text = str(event.get("text") or "").strip()
    if not text and kind not in ("file_change", "command_execution", "tool_use"):
        return ""

    if kind == "reasoning":
        if not _SHOW_REASONING:
            return ""
        head = _trunc(_first_line(text), 200)
        return theme.dim("  ⋯ " + head)

    if kind in ("assistant_message", "agent_message", "message"):
        # Preserve multi-line model speech but trim each line. The full
        # text was already truncated to 600 chars upstream.
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return ""
        bar = theme.cyan("▌")
        out: list[str] = []
        for ln in lines:
            out.append(f"{bar} {theme.bold(_trunc(ln, 240))}")
        return "\n".join(out)

    if kind == "command_execution":
        action = str(event.get("action_summary") or "").strip()
        if action:
            return theme.dim("  ▸ " + _trunc(action, 200))
        # Strip the ``/bin/bash -lc 'cmd'`` wrapper codex always emits so
        # the user sees the actual command when no action summary exists.
        cmd = _strip_shell_wrapper(_first_line(text))
        return theme.dim("  ▸ $ " + _trunc(cmd, 200))

    if kind == "tool_use":
        return theme.dim("  ▸ ⚙ " + _trunc(_first_line(text), 200))

    if kind == "file_change":
        return theme.dim("  ▸ ✎ " + _trunc(_first_line(text), 200))

    # Unknown progress kind — fall back to dim single-liner.
    return theme.dim("  ▸ " + _trunc(_first_line(text) or kind, 200))


def _first_line(text: str) -> str:
    for ln in text.splitlines():
        s = ln.strip()
        if s:
            return s
    return text.strip()


def _strip_shell_wrapper(cmd: str) -> str:
    # codex stream-json wraps commands as ``/bin/bash -lc 'real cmd'`` or
    # ``/bin/bash -c "real cmd"``. Unwrap one layer of quotes so the
    # operations lane shows the command the model actually intended.
    prefixes = ("/bin/bash -lc ", "/bin/bash -c ", "bash -lc ", "bash -c ", "sh -c ")
    for p in prefixes:
        if cmd.startswith(p):
            inner = cmd[len(p):].strip()
            if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in ("'", '"'):
                return inner[1:-1]
            return inner
    return cmd


def _looks_like_diff(text: str) -> bool:
    """True iff ``text`` is a unified diff — detected STRUCTURALLY by a hunk
    header or file-header line, never by guessing at content. Guards the
    ``+``/``-`` line colouring so prose bullets ("- item") in a plain review
    are never mis-tinted red/green.
    """
    for ln in text.splitlines():
        s = ln.lstrip()
        if (
            s.startswith("@@ ")
            or s.startswith("diff --git ")
            or s.startswith("--- a/")
            or s.startswith("+++ b/")
        ):
            return True
    return False


def _colorize_diff_line(line: str, *, theme: Theme) -> str:
    """Line-level +/- diff colouring: added lines green, removed red, file
    headers dim, hunk headers cyan. Word-level intra-line diff is deliberately
    NOT done (it needs a diff library; this stays stdlib-only)."""
    stripped = line.lstrip()
    if stripped[:3] in ("+++", "---"):      # file headers — recede
        return theme.dim(line)
    if stripped[:2] == "@@":                 # hunk header — orient
        return theme.cyan(line)
    if stripped[:1] == "+":
        return theme.green(line)
    if stripped[:1] == "-":
        return theme.red(line)
    return line                              # context / prose — untouched
