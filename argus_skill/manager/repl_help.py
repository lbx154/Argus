"""Slash-command catalog and prompt-frame rendering for the Manager REPL."""

from __future__ import annotations

from typing import Any

#: Slash commands surfaced to completion + the command palette + /help. (label,
#: one-line help). The dispatcher in ``dispatch_command`` is the source of truth;
#: this list mirrors it for the UI. Keep in sync when adding a command.
#:
#: A description of ``"alias of /x"`` marks a pure alias: ``_help_command_rows``
#: folds it into ``/x``'s row instead of listing it separately, so /help stays
#: readable while every real spelling still completes and is matched by the
#: unknown-command "did you mean" hint.
SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/help", "show this command reference"),
    ("/commands", "alias of /help"),
    ("/status", "overall state: daemon, four roles, backlog, journal summary"),
    ("/roles", "live manager/planner/engineer/reviewer status + backend/model"),
    ("/journal", "recent task journal entries (/journal N for more, default 10)"),
    ("/backlog", "pending tasks (/backlog all to include done/skipped)"),
    ("/add", "queue a task: /add <objective> [--once] [--cycles=N] [--budget=$X]"),
    ("/plan", "preview how an objective would be broken down, without queuing it"),
    ("/stop", "stop a task's auto-continue: /stop <item_id>"),
    ("/done", "mark a task done: /done <item_id>"),
    ("/skip", "alias of /done"),
    ("/rm", "alias of /done"),
    ("/note", "append a note to the journal: /note <text>"),
    ("/nudge", "inject guidance into the running task: /nudge <message>"),
    ("/inject", "alias of /nudge"),
    ("/notify", "alias of /nudge"),
    ("/run", "attach and live-follow the daemon draining the backlog"),
    ("/daemon", "control this cockpit's executor: /daemon [start|stop|status]"),
    ("/daemons", "list every live daemon across all projects"),
    ("/attach", "read-only follow another project's daemon: /attach <session-id>"),
    ("/resume", "switch into the previous conversation (/resume list = all; /resume <id> = one)"),
    ("/doctor", "diagnose + fix \"why isn't anything running\""),
    ("/backend", "view/change the runner backend"),
    ("/config", "view/change this session's defaults (cycles, budget, effort...)"),
    ("/continuous", "manage auto-generate-new-work mode: /continuous [start|stop|status]"),
    ("/start", "shortcut for /continuous start <objective>"),
    ("/identity", "view/edit the operator identity card"),
    ("/reset", "drop the carried session thread; the next turn starts fresh"),
    ("/skills", "inspect/promote a skill: /skills [ls|promote <name>]"),
    ("/exit", "leave the cockpit (also: Ctrl-D, `退出`)"),
]

#: Grouping of the *primary* (non-alias) commands above, purely for /help
#: layout. Any command missing here still shows up (see ``_help_command_rows``)
#: so a forgotten entry degrades to "unsorted", never to "invisible".
_HELP_SECTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("Everyday", ("/status", "/roles", "/journal", "/backlog")),
    ("Task management", ("/add", "/plan", "/stop", "/done", "/note", "/nudge", "/run")),
    ("Daemon & diagnostics", ("/daemon", "/daemons", "/attach", "/resume", "/doctor")),
    ("Configuration", ("/backend", "/config", "/continuous", "/start", "/identity", "/reset", "/skills")),
    ("Other", ("/help", "/exit")),
]


def _help_command_rows() -> dict[str, tuple[str, str]]:
    """Fold alias rows (``"alias of /x"``) from ``SLASH_COMMANDS`` into their
    primary command. Returns ``{primary_cmd: (display_label, description)}``,
    where ``display_label`` includes any aliases (e.g. ``"/done (= /skip, /rm)"``).
    """
    aliases: dict[str, list[str]] = {}
    primaries: dict[str, str] = {}
    for cmd, desc in SLASH_COMMANDS:
        if desc.startswith("alias of "):
            aliases.setdefault(desc[len("alias of "):].strip(), []).append(cmd)
        else:
            primaries[cmd] = desc
    rows: dict[str, tuple[str, str]] = {}
    for cmd, desc in primaries.items():
        extra = aliases.get(cmd)
        label = f"{cmd}  (= {', '.join(extra)})" if extra else cmd
        rows[cmd] = (label, desc)
    return rows


def _closest_slash_command(cmd: str) -> str | None:
    """Best-effort "did you mean" suggestion for an unrecognized slash command,
    matched against every real spelling in ``SLASH_COMMANDS`` (aliases
    included) so a mistyped alias still resolves to a useful hint."""
    import difflib

    names = [c for c, _ in SLASH_COMMANDS]
    matches = difflib.get_close_matches(cmd.lower(), names, n=1, cutoff=0.5)
    return matches[0] if matches else None


def _bottom_hint_line(theme: Any, status: str) -> str:  # noqa: ANN001
    """Bottom border of the input frame: left hint · a dim rule fill · the
    right-aligned backend/model status — one row under the input.

    The dim ``─`` rule between the hint and the status makes this read as the
    bottom edge of the input frame (matching the ``╭─`` rule above it, à la
    Claude Code's bordered input), instead of the old empty-space padding.
    The pattern (hint left, status right, one row) was confirmed live via a
    pty + pyte terminal emulator against both Codex CLI and Claude Code. The
    right side (rule + status) is dropped on narrow terminals rather than
    truncated into nonsense.

    Uses ``theme.live_width()`` (re-queries the tty), NOT the cached
    ``theme.width`` snapshot taken once at startup: this line is padded to
    exactly fill the terminal, and every "move up N rows" redraw around it
    (the live-cockpit panel, the readline handoff) assumes one physical row
    per logical line. If the operator resizes their terminal — or a stale
    ``COLUMNS`` env var disagreed with the tty from the start — a line padded
    for the WRONG width wraps into two physical rows and desyncs that row
    count, confirmed live via pty+pyte: the input row and a wrapped fragment
    of this very hint line visually collided on the same row
    ("╰─ 你er send · /help commands"). The rule fill is sized off the SAME
    live width, so it preserves that one-physical-row contract exactly.
    """
    from ..cli.theme import BOX, visible_len
    width = theme.live_width()
    left = theme.dim("Enter send · /help commands")
    if not status or width < 60:
        return "  " + left
    # fill = width − 2-col left margin − hint − status − 2 spaces (one each
    # side of the rule, keeping text off the line). Total visible == width−2,
    # so the line never reaches the edge and never wraps.
    fill = width - visible_len(left) - visible_len(status) - 6
    if fill < 1:
        # Not enough room for a rule — fall back to the plain padded form
        # (still exactly width−2 visible), preserving the one-row contract.
        pad = width - visible_len(left) - visible_len(status) - 4
        if pad < 1:
            return "  " + left
        return "  " + left + (" " * pad) + status
    rule = theme.dim(BOX["h"] * fill)
    return "  " + left + " " + rule + " " + status


def _top_frame_line(theme: Any, label: str) -> str:  # noqa: ANN001
    """Top edge of the input frame: ``╭─ <label> ─────…`` — the ``╭─`` corner
    plus a bold label, then a dim ``─`` rule filling the live width, matching
    the bottom edge drawn by :func:`_bottom_hint_line`. Together with the
    ``╰─ `` input prefix (the bottom-left corner) this brackets the input in a
    modern framed style.

    Sized to ``theme.live_width()`` (width−2 margin) so it stays exactly ONE
    physical row and never wraps — the same contract the cursor-math redraws
    around the prompt rely on. Crucially this changes only ``banner_row``'s
    WIDTH, never the multi-line prompt STRUCTURE, so ``_split_readline_safe_prompt``
    and the ``cursor_up_and_forward`` column math (which key off the unchanged
    3-column ``╰─ `` input prefix, not this line) are untouched.
    """
    from ..cli.theme import BOX, visible_len
    corner = theme.cyan(BOX["tl"] + BOX["h"] + " ")   # "╭─ "
    head = corner + label + " "
    width = theme.live_width()
    fill = width - visible_len(head) - 2
    if fill < 1:
        # Too narrow for a rule — just the corner + label (still one row).
        return corner + label
    return head + theme.dim(BOX["h"] * fill)

__all__ = [
    "SLASH_COMMANDS",
    "_HELP_SECTIONS",
    "_bottom_hint_line",
    "_closest_slash_command",
    "_help_command_rows",
    "_top_frame_line",
]
