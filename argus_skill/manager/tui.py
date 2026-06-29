"""Full-screen TUI for the argus-skill REPL (opencode / Claude-Code style).

A prompt_toolkit Application that wraps the existing dispatch + handlers — the
business logic stays in ``repl.py``; this only renders. Layout:

  ┌ banner + status bar ────────────────────────────┐
  │ tasks (backlog) │ event stream (events.jsonl)    │
  │                 │ … spinner ●engineer r3/6 $0.42 │
  ├ input ──────────────────────────────────────────┤

A background thread tails ``events.jsonl`` and pushes rendered lines into the
right pane; the input box dispatches each submitted line through
``repl.dispatch_command`` with stdout captured into the same pane. Non-TTY,
NO_COLOR, ``ARGUS_SKILL_NO_TUI=1`` or a missing prompt_toolkit all fall back to
the classic line REPL (the caller checks :func:`tui_available`).
"""
from __future__ import annotations

import io
import os
import sys
import threading
import time
from contextlib import redirect_stdout
from typing import Any


def tui_available() -> bool:
    """True when a rich TUI is sensible: a real TTY, color allowed, not opted out,
    and prompt_toolkit importable. Any miss → caller keeps the line REPL."""
    if os.environ.get("ARGUS_SKILL_NO_TUI", "").strip() in ("1", "true", "yes", "on"):
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if not (sys.stdout.isatty() and sys.stdin.isatty()):
        return False
    try:
        import prompt_toolkit  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def run_manager_tui(mem: Any, chat_state: dict, global_root: Any) -> int:
    """Drive the full-screen TUI. Returns the REPL exit code."""
    from prompt_toolkit import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.completion import FuzzyWordCompleter
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.dimension import D

    from . import repl as _repl

    life_dir = _repl._life_dir_for(mem)
    feed: list[tuple[str, str]] = []  # (style, text)
    spin = {"i": 0, "stage": "idle", "round": "", "cost": chat_state.get("last_cost_usd")}
    stop = threading.Event()

    def push(text: str, style: str = "") -> None:
        for ln in str(text).splitlines() or [""]:
            feed.append((style, ln))
        del feed[:-2000]  # bounded scrollback

    # ---- header / status ------------------------------------------------
    def header() -> list[tuple[str, str]]:
        from ..daemon.life_worker import read_daemon_status
        try:
            st = read_daemon_status(life_dir)
            d = f"⚡ pid {st.pid}" if getattr(st, "alive", False) else "○ no daemon"
        except Exception:  # noqa: BLE001
            d = "○ no daemon"
        pend = len(mem.backlog.pending()) if hasattr(mem, "backlog") else 0
        cost = spin["cost"]
        c = f"${cost:.2f}" if isinstance(cost, (int, float)) else "$0.00"
        sp = _SPINNER[spin["i"] % len(_SPINNER)] if spin["stage"] != "idle" else "●"
        return [("bold cyan", " argus "), ("", f"· {d} · {pend} pending · {c} · "),
                ("yellow", f"{sp} {spin['stage']} {spin['round']}".rstrip())]

    def tasks_text() -> list[tuple[str, str]]:
        out = [("bold", " TASKS\n")]
        try:
            for it in (mem.backlog.pending() or [])[:14]:
                out.append(("cyan", f" • {it.id[:6]} "))
                out.append(("", f"{it.objective[:30]}\n"))
        except Exception:  # noqa: BLE001
            pass
        return out or [("", " (none)\n")]

    def feed_text() -> list[tuple[str, str]]:
        return [(s or "", t + "\n") for s, t in feed[-400:]] or [("", "")]

    # ---- background event tail -----------------------------------------
    def tail() -> None:
        import json

        from ..apps.cli._follow import _follow_layer_from_event, _format_follow_event
        p = life_dir / "events.jsonl"
        off = 0
        layer = "engineer"
        while not stop.is_set():
            try:
                with p.open() as fh:
                    fh.seek(off)
                    chunk = fh.read()
                    off = fh.tell()
            except OSError:
                time.sleep(0.4)
                continue
            for ln in chunk.splitlines():
                try:
                    e = json.loads(ln)
                except Exception:  # noqa: BLE001
                    continue
                t = str(e.get("type") or "")
                if t in ("life.phase.started", "round.start"):
                    spin["stage"] = "engineer"
                elif t == "round.review.started":
                    spin["stage"] = "reviewer"
                elif t == "life.mission.completed":
                    spin["stage"] = "idle"
                    spin["cost"] = e.get("cost_usd") or spin["cost"]
                layer = _follow_layer_from_event(e, layer)
                r = _format_follow_event(e, layer)
                if r:
                    push(r)
            spin["i"] += 1
            time.sleep(0.4)
    threading.Thread(target=tail, daemon=True).start()

    kb = KeyBindings()
    history = FileHistory(str(life_dir / ".repl_history"))
    inp = Buffer(history=history, multiline=False,
                 completer=FuzzyWordCompleter([c for c, _ in _repl.SLASH_COMMANDS]))

    @kb.add("enter")
    def _(ev) -> None:
        line = inp.text.strip()
        inp.reset()
        if not line:
            return
        if line in ("/exit", "/quit", ":q"):
            stop.set()
            ev.app.exit(0)
            return
        push(f"argus › {line}", "bold cyan")
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                _repl.dispatch_command(line, line, mem, chat_state, global_root, _repl_theme())
        except Exception as exc:  # noqa: BLE001
            push(f"error: {exc}", "red")
        if buf.getvalue().strip():
            push(buf.getvalue())
        if chat_state.get("blocked_question"):
            push(f"❓ 需要你定夺：{chat_state['blocked_question']}（直接回复即可）", "bold yellow")

    @kb.add("c-c")
    @kb.add("c-d")
    def _(ev) -> None:
        stop.set()
        ev.app.exit(0)

    app = Application(
        layout=Layout(HSplit([
            Window(FormattedTextControl(header), height=1, style="reverse"),
            VSplit([
                Window(FormattedTextControl(tasks_text), width=D(weight=1)),
                Window(width=1, char="│"),
                Window(FormattedTextControl(feed_text), wrap_lines=True, width=D(weight=3)),
            ]),
            Window(height=1, char="─"),
            VSplit([Window(FormattedTextControl([("bold cyan", "› ")]), width=2),
                    Window(BufferControl(inp))], height=1),
        ])),
        key_bindings=kb, full_screen=True, refresh_interval=0.3, mouse_support=True,
    )
    try:
        app.run()
    finally:
        stop.set()
    return 0


def _repl_theme() -> Any:
    from ..cli.theme import Theme
    return Theme.auto(force=False)  # plain (no ANSI) so captured handler text is clean
