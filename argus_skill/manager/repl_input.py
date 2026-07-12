"""Terminal input engines for the Manager REPL."""

from __future__ import annotations

import os
import re
import sys
from typing import Any

from .repl_help import SLASH_COMMANDS


def _life_dir_for(mem: Any):
    from .repl import _life_dir_for as resolve
    return resolve(mem)


def _live_cockpit_enabled() -> bool:
    from .repl import _live_cockpit_enabled as enabled
    return enabled()

def _drain_available_bytes(fd: int, first: bytes) -> bytes:
    """After ``first`` byte(s) arrive, non-blocking-drain any more that are
    already buffered (multibyte CJK char, fast typing, paste) so the first
    keystroke is captured whole and echoes correctly when seeded into readline."""
    import select as _select
    data = bytes(first)
    while len(data) < 8192:
        try:
            r, _, _ = _select.select([fd], [], [], 0.0)
        except (OSError, ValueError):
            break
        if not r:
            break
        try:
            more = os.read(fd, 4096)
        except OSError:
            break
        if not more:
            break
        data += more
    return data


def _live_cockpit_will_activate(mem: Any) -> bool:
    """True iff ``read_message_with_live_cockpit`` will actually render the
    fancy cbreak-mode cockpit panel for the current turn, rather than
    silently falling back to a plain ``input()`` call underneath.

    Mirrors — as a single shared check — every early-return guard at the top
    of that function (env opt-out, TTY-ness, ``termios`` availability,
    resolvable life-dir, a live daemon, a tall-enough terminal). This exists
    because the REPL's own prompt construction must decide, BEFORE calling
    ``read_message_with_live_cockpit``, whether it is safe to pre-print a
    plain-prompt layout itself and hand that function a bare single-row
    prompt: ``_live_cockpit_enabled()`` alone is NOT sufficient for that
    decision, since it only reflects the ``ARGUS_SKILL_COCKPIT_LIVE`` opt-out
    and says nothing about the daemon/terminal-size conditions checked here —
    trusting it alone let a stale multi-row prompt (meant only for the
    fancy-panel path, which manages its own redraws) slip into the plain
    ``input()`` fallback and corrupt the display the moment readline did any
    internal redraw (confirmed live: the "╰─ " prefix vanished mid-keystroke
    whenever the daemon happened to be down, e.g. under ``--no-daemon``).
    Keeping this as one shared helper means the two call sites can never
    drift out of sync on what "will the fancy panel actually show" means.
    """
    if not _live_cockpit_enabled():
        return False
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        import termios  # noqa: F401
        import tty  # noqa: F401
    except Exception:  # noqa: BLE001 — no POSIX terminal control
        return False
    try:
        life_dir = _life_dir_for(mem)
    except Exception:  # noqa: BLE001
        life_dir = None
    if life_dir is None:
        return False
    try:
        from ..daemon.life_worker import read_daemon_status
        st0 = read_daemon_status(life_dir)
        if not (getattr(st0, "alive", False) and getattr(st0, "pid", None)):
            return False
    except Exception:  # noqa: BLE001
        return False
    try:
        import shutil
        rows = shutil.get_terminal_size((80, 24)).lines
    except Exception:  # noqa: BLE001
        rows = 24
    if rows < 16:
        return False
    return True


_CURSOR_UP_RE = re.compile(r"\x1b\[(\d*)A")
_CURSOR_DOWN_RE = re.compile(r"\x1b\[(\d*)B")


def _visual_row_delta(text: str) -> int:
    """Net terminal ROWS ``text`` moves the cursor down when printed as-is.

    Plain ``str.count("\\n")`` underestimates this whenever ``text`` also
    embeds a cursor-repositioning escape (e.g. ``theme.cursor_up_and_forward``,
    used by the caller of :func:`read_message_with_live_cockpit` to land the
    cursor back on the input row after printing a multi-line prompt for
    readline's benefit): the trailing ``\\x1b[<n>A`` moves the cursor UP by
    ``n`` rows AFTER the newlines already advanced it down, so the true final
    row is ``n`` less than the newline count alone suggests. Using the
    newline-only count to erase-and-redraw this block on the next refresh
    then overshoots upward by that same ``n`` every cycle, eating one extra
    (real, previously-printed) row above the block each time — exactly the
    "banner disappears one line per refresh" bug this fixes. Handles
    ``\\x1b[<n>B`` (cursor down) symmetrically for completeness.
    """
    delta = text.count("\n")
    for m in _CURSOR_UP_RE.finditer(text):
        delta -= int(m.group(1) or 1)
    for m in _CURSOR_DOWN_RE.finditer(text):
        delta += int(m.group(1) or 1)
    return delta


def _split_readline_safe_prompt(prompt: str, theme: Any) -> tuple[str, str] | None:
    """Turn a ``banner\\ninput_prefix\\nhint+cursor-escape`` prompt (built for
    the live-cockpit's own cursor-controlled ``_block()`` rendering) into a
    ``(pre_print_text, bare_input_prompt)`` pair that's safe to hand to
    input()/readline instead.

    Feeding the ORIGINAL 3-line, escape-laden string straight to readline
    corrupts the display the instant readline does its own internal redraw:
    readline counts the embedded ``"\\n"``s to learn where editing starts,
    but the trailing ``cursor_up_and_forward`` escape moves the cursor to a
    DIFFERENT row than that count implies (live-reproduced via pty+pyte:
    typing "hello" rendered progressively as "h" -> "he" -> "el h" ->
    "ellh" -> "ello", eating characters and the input-row prefix). This is
    the same "Round 1" class of bug already fixed for the non-live-cockpit
    prompt path — reproduced here: print everything except the input row
    directly, land the cursor on a blank input row via
    ``cursor_up_and_forward(2, 0)``, and return the bare input-row prefix
    for the caller to use as the real prompt.

    Returns ``None`` (caller should use ``prompt`` as-is) if it is not
    shaped as expected (defensive — must never break the input path)."""
    from ..apps._input_helpers import _ANSI_RE

    parts = prompt.split("\n", 2)
    if len(parts) != 3:
        return None
    banner_line, input_prefix, rest = parts
    # ``_ANSI_RE`` only matches bracketed ``\x1b[...`` sequences, so it strips
    # the ``\x1b[<n>A`` / ``\x1b[<n>C`` halves of a trailing
    # ``cursor_up_and_forward`` escape but NOT the bare "\r" it unconditionally
    # emits between them (``theme.cursor_up_and_forward`` always appends "\r"
    # to reset to column 0, even when ``forward=0`` skips the "\x1b[nC" part
    # entirely) — that stray literal carriage return is not ANSI, so the
    # regex leaves it sitting in ``rest_clean``. Currently harmless only
    # because it happens to be followed immediately by a row-advancing "\n"
    # (both reset column 0; ONLCR makes the "\n" alone equivalent), but
    # relying on that adjacency is fragile — a hint line has no legitimate
    # reason to contain a raw "\r", so drop it explicitly.
    rest_clean = _ANSI_RE.sub("", rest).replace("\r", "")
    pre_print = (
        banner_line + "\n"
        + "\n"  # blank placeholder input row — filled in by input() below
        + rest_clean + "\n"
        + theme.cursor_up_and_forward(2, 0)
    )
    return pre_print, input_prefix


def _build_slash_completer():
    """A prompt_toolkit completer offering the slash commands (with their
    one-line descriptions) as a live dropdown — but ONLY when the current line
    starts with ``/``, so ordinary chat never pops a menu. Primary spellings
    only; ``alias of …`` rows are folded out (their target already shows)."""
    from prompt_toolkit.completion import Completer, Completion

    class _SlashCompleter(Completer):
        def get_completions(self, document, _complete_event):  # noqa: ANN001
            word = document.text_before_cursor
            if not word.startswith("/"):
                return
            for cmd, desc in SLASH_COMMANDS:
                if desc.startswith("alias of "):
                    continue
                if cmd.startswith(word):
                    yield Completion(
                        cmd,
                        start_position=-len(word),
                        display=cmd,
                        display_meta=desc,
                    )

    return _SlashCompleter()


def _get_prompt_session(chat_state: dict[str, Any], mem: Any):
    """Lazily build + cache the prompt_toolkit ``PromptSession`` used for the
    line REPL's TTY input: live slash-command completion + per-session history.
    Cached on ``chat_state`` so history/completer persist for the session."""
    sess = chat_state.get("prompt_session")
    if sess is not None:
        return sess
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory, InMemoryHistory

    try:
        hist: Any = FileHistory(str(_life_dir_for(mem) / "repl_history"))
    except Exception:  # noqa: BLE001 — history is best-effort
        hist = InMemoryHistory()

    # prompt_toolkit only auto-triggers completion on INSERT (buffer.py) and a
    # lone exact match is discarded — so editing a slash command mid-line (e.g.
    # backspace then retype) leaves no dropdown. Re-run completion after a
    # delete when the line is a slash command, so the menu always reflects the
    # current text.
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    def _retrigger_slash(buf: Any) -> None:
        try:
            if buf.text.lstrip().startswith("/"):
                buf.complete_state = None
                buf.start_completion()
        except Exception:  # noqa: BLE001 — completion is best-effort
            pass

    @kb.add("backspace")
    def _(event: Any) -> None:  # noqa: ANN401
        event.current_buffer.delete_before_cursor()
        _retrigger_slash(event.current_buffer)

    @kb.add("delete")
    def _(event: Any) -> None:  # noqa: ANN401
        event.current_buffer.delete()
        _retrigger_slash(event.current_buffer)

    sess = PromptSession(
        completer=_build_slash_completer(),
        history=hist,
        complete_while_typing=True,
        key_bindings=kb,
    )
    chat_state["prompt_session"] = sess
    return sess


def _use_prompt_toolkit_input() -> bool:
    """True iff the default TTY input should use prompt_toolkit — the default
    cockpit input engine: `/` completion + honest Ctrl-C + resumable
    conversations, all in one driver. This ONLY controls the INPUT engine;
    whether the live 4-role panel is also drawn above it is a SEPARATE flag
    (``_live_cockpit_enabled`` — see ``_panel_text`` in
    :func:`read_message_prompt_toolkit`) that this function deliberately does
    not consult. Both happen to default to "on" today, but keeping them
    independent means turning either one off (``ARGUS_SKILL_NO_PROMPT_TOOLKIT=1``
    for the input engine, ``ARGUS_SKILL_COCKPIT_LIVE=0`` for the panel) never
    silently flips the other. Off for: non-TTY / piped stdin,
    ``ARGUS_SKILL_NO_PROMPT_TOOLKIT=1`` (falls back to the legacy cbreak panel /
    plain reader), or a missing prompt_toolkit."""
    if os.environ.get("ARGUS_SKILL_NO_PROMPT_TOOLKIT") == "1":
        return False
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
    except Exception:  # noqa: BLE001
        return False
    try:
        import prompt_toolkit  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def read_message_prompt_toolkit(
    prompt: str,
    mem: Any,
    theme: Any,
    chat_state: dict[str, Any],
) -> str | None:
    """Read one operator line via prompt_toolkit — the unified cockpit input.

    Prints a status line ABOVE the input (as scrollback, repainted each turn)
    and hands prompt_toolkit only the short ╭─/╰─ box, so the ``/``
    completion menu positions cleanly BELOW the input — one engine, no
    cbreak/prompt_toolkit tug-of-war. That status line is the full live
    four-role panel by default (``_live_cockpit_enabled``, same flag the
    legacy cbreak engine honours) — this input engine and the panel are
    independent choices (see ``_use_prompt_toolkit_input``), but both
    default to showing it automatically; ``ARGUS_SKILL_COCKPIT_LIVE=0``
    drops back to the same lightweight one-liner the plain engine falls
    back to (``format_prompt_status_line``).

    Returns the line, or ``None`` on EOF (Ctrl-D). Ctrl-C raises
    ``KeyboardInterrupt`` (the caller arms double-Ctrl-C exit). Any init/render
    failure falls back to the plain, always-reliable reader."""
    from ..apps._input_helpers import read_pasted_message

    try:
        from prompt_toolkit import ANSI

        session = _get_prompt_session(chat_state, mem)
    except Exception:  # noqa: BLE001 — never let the input path die
        return read_pasted_message(prompt)

    # Resolve the life-dir once; the panel callable re-renders fresh each refresh.
    try:
        life_dir = _life_dir_for(mem)
    except Exception:  # noqa: BLE001
        life_dir = None

    def _panel_text() -> str:
        """Status text drawn above the prompt (empty if unavailable).

        Full four-role panel by default (``_live_cockpit_enabled``); falls
        back to the same compact one-liner the plain engine shows if the
        operator opts out (``ARGUS_SKILL_COCKPIT_LIVE=0``) or panel
        rendering itself fails, so turning the panel off doesn't also erase
        all status visibility."""
        if life_dir is None:
            return ""
        if _live_cockpit_enabled():
            try:
                import shutil

                from ..cli.roles_status import render_roles_snapshot
                width = shutil.get_terminal_size((80, 24)).columns
                return render_roles_snapshot(life_dir, theme, width=width) + "\n"
            except Exception:  # noqa: BLE001 — fall back to the one-liner below
                pass
        try:
            from ..cli.roles_status import format_prompt_status_line
            s = format_prompt_status_line(theme, life_dir=life_dir)
            return (s + "\n") if s else ""
        except Exception:  # noqa: BLE001
            return ""

    # Print the status ABOVE the input as ordinary scrollback — NOT folded into
    # the prompt `message`. Folding a tall panel into the message pushed the
    # input line near the bottom, so prompt_toolkit flipped the `/` completion
    # menu ABOVE the input. With it printed above and only the short ╭─/╰─ box
    # handed to prompt_toolkit, the menu positions cleanly BELOW the input
    # (its normal behaviour). Trade-off: it repaints each turn (when you
    # return to the prompt), not sub-second while idle.
    try:
        panel = _panel_text()
        if panel:
            sys.stdout.write(panel)
            sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass

    try:
        return session.prompt(ANSI(prompt))
    except EOFError:
        return None
    except KeyboardInterrupt:
        # Intentionally propagates to the caller (arms the double-Ctrl-C exit).
        raise
    except Exception:  # noqa: BLE001 — never let a PT hiccup kill the cockpit
        # An odd terminal, a resize race, or any prompt_toolkit-internal error
        # must NOT crash the REPL. Disable PT for the rest of the session and
        # fall back to the plain, always-reliable reader for this and every
        # subsequent turn.
        chat_state.pop("prompt_session", None)
        chat_state["prompt_toolkit_disabled"] = True
        return read_pasted_message(prompt)


def read_message_with_live_cockpit(
    prompt: str,
    mem: Any,
    theme: Any,
    *,
    interval: float = 1.0,
) -> str | None:
    """Read one operator message while a LIVE four-role cockpit is pinned above
    the prompt — so the operator always sees what Manager / Planner / Engineer /
    Reviewer are doing WITHOUT ever typing ``/roles``.

    The panel refreshes in place ~1×/s; the moment the operator starts typing it
    is dismissed and the keystroke is handed to the normal (readline-editable)
    input path — CJK-safe. Degrades to a plain prompt on any of: not a TTY, no
    ``termios``, ``ARGUS_SKILL_COCKPIT_LIVE=0``, no life-dir, no live daemon, a
    too-short terminal, or any unexpected error (the core input path is never
    put at risk). ON by default — showing multi-role progress automatically,
    with no manual step required, is the whole point; set
    ``ARGUS_SKILL_COCKPIT_LIVE=0`` to opt back OUT to the plain prompt (e.g.
    for scripting/logging where a redrawing panel is unwanted)."""
    from ..apps._input_helpers import read_pasted_message
    if not _live_cockpit_will_activate(mem):
        return read_pasted_message(prompt)
    # _live_cockpit_will_activate() already proved these import cleanly (that is
    # part of what it checks), but a successful import in ITS scope does not
    # make the names available here — each scope that uses termios/tty needs
    # its own import.
    import termios
    import tty
    life_dir = _life_dir_for(mem)

    # This path is ON by default. It uses terminal cursor rewrites — every
    # unsupported condition above degrades to the boring, always-reliable
    # plain input path instead, so enabling it by default never risks the
    # core input path itself.
    def _daemon_right() -> str:
        try:
            from ..daemon.life_worker import read_daemon_status
            st = read_daemon_status(life_dir)
            if getattr(st, "alive", False) and getattr(st, "pid", None):
                lab = f"● daemon {st.pid}"
                return theme.bold_green(lab) if theme is not None else lab
        except Exception:  # noqa: BLE001
            pass
        lab = "○ no daemon"
        return theme.gray(lab) if theme is not None else lab

    import select as _select

    from ..cli.roles_status import render_roles_snapshot

    hint = ("Just start typing to chat · Ctrl-C refresh · Ctrl-D exit"
            if theme is not None else "(type to chat · Ctrl-D exits)")

    def _block() -> str:
        # Re-queried every refresh (not hoisted above the loop): the operator
        # can resize their terminal while this panel idles, and a width
        # baked in once at the top would silently wrap the padded
        # "roles · activity" header line on the next redraw — desyncing the
        # "move up N rows" erase math the same way a stale ``theme.width``
        # did (see ``Theme.live_width``).
        width = theme.live_width() if theme is not None else 80
        panel = render_roles_snapshot(life_dir, theme, width=width,
                                      header_right=_daemon_right())
        hint_line = "  " + (theme.dim(hint) if theme is not None else hint)
        return panel + "\n" + hint_line + "\n" + prompt

    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except Exception:  # noqa: BLE001
        return read_pasted_message(prompt)

    raw: bytes = b""
    interrupted = False
    up = 0
    try:
        tty.setcbreak(fd)  # disables ICANON + ECHO, keeps ISIG (Ctrl-C → SIGINT)
        sys.stdout.write("\x1b[?25l")
        block = _block()
        sys.stdout.write(block)
        sys.stdout.flush()
        up = _visual_row_delta(block)
        while True:
            try:
                r, _, _ = _select.select([fd], [], [], interval)
            except (OSError, ValueError):
                r = [fd]  # cannot poll → just read
            if r:
                try:
                    first = os.read(fd, 1)
                except OSError:
                    first = b""
                raw = _drain_available_bytes(fd, first)
                break
            block = _block()
            sys.stdout.write("\r\x1b[%dA\x1b[J" % up)  # up to panel top, clear region
            sys.stdout.write(block)
            sys.stdout.flush()
            up = _visual_row_delta(block)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        try:
            sys.stdout.write("\r\x1b[%dA\x1b[J" % up)  # erase the transient cockpit
        except Exception:  # noqa: BLE001
            pass
        try:
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:  # noqa: BLE001
            pass

    # ── classify the first keystroke ──────────────────────────────────────────
    # ``prompt`` here is still the multi-line, escape-laden string the caller
    # built for THIS function's own _block() rendering (banner row + "\n" +
    # input-row prefix + "\n" + bottom hint line + a trailing
    # cursor_up_and_forward escape) — never meant to be handed to
    # input()/readline directly. Every return below that hands off to
    # read_pasted_message(prompt) used to do exactly that, corrupting the
    # display the instant readline redrew internally: readline counts the
    # embedded "\n"s to learn where editing starts, but the trailing escape
    # moves the ACTUAL cursor to a different row than that count implies —
    # the exact "Round 1" class of bug already fixed for the non-live-cockpit
    # prompt path (see the caller). Reproduce that fix's pattern here: print
    # everything except the input row directly, land the cursor on a blank
    # input row, and hand read_pasted_message only the bare input-row prefix.
    _split = _split_readline_safe_prompt(prompt, theme)
    if _split is not None:
        _pre_print, prompt = _split
        sys.stdout.write(_pre_print)
        sys.stdout.flush()
    if interrupted:
        # Ctrl-C while idle: propagate so the REPL loop arms double-Ctrl-C exit
        # (the panel re-renders on the next loop iteration anyway).
        raise KeyboardInterrupt
    if raw == b"" or raw[:1] == b"\x04":
        return None  # Ctrl-D / EOF → quit
    if raw[:1] in (b"\r", b"\n"):
        return ""  # bare Enter → refresh
    if raw[:1] == b"\x1b" or raw[:1] in (b"\x7f", b"\x08"):
        # escape sequence (arrow/fn) or backspace → dismiss to a clean prompt
        return read_pasted_message(prompt)
    seed = raw.decode("utf-8", "replace")
    seed = "".join(ch for ch in seed if ch >= " " or ch == "\t").strip("\ufffd")
    if not seed:
        return read_pasted_message(prompt)
    # Seed readline so the first keystroke shows and stays fully editable.
    try:
        import readline

        def _hook() -> None:
            readline.insert_text(seed)
            readline.redisplay()

        readline.set_pre_input_hook(_hook)
        try:
            return read_pasted_message(prompt)
        finally:
            readline.set_pre_input_hook(None)
    except Exception:  # noqa: BLE001 — readline missing → prepend the seed
        rest = read_pasted_message(prompt)
        if rest is None:
            return seed
        return seed + rest

__all__ = [
    "_build_slash_completer", "_drain_available_bytes",
    "_get_prompt_session", "_live_cockpit_will_activate",
    "_split_readline_safe_prompt", "_use_prompt_toolkit_input",
    "_visual_row_delta", "read_message_prompt_toolkit",
    "read_message_with_live_cockpit",
]
