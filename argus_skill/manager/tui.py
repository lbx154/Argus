"""Full-screen TUI for the argus-skill REPL (opencode / Claude-Code style).

A prompt_toolkit Application wrapping the existing handlers — business logic
stays in ``repl.py``; this only renders. Layout:

  ╭ status bar (daemon · pending · cost · spinner stage) ────────────╮
  │ ╭ TASKS ───────╮ ╭ ACTIVITY ──────────────────────────────────╮ │
  │ │ • a1b2 …      │ │ event stream (events.jsonl, live)          │ │
  │ ╰──────────────╯ ╰────────────────────────────────────────────╯ │
  ╰ › input (manager triages first) ─────────────────────────────────╯

Responsiveness: every submitted line is dispatched on a WORKER THREAD (so the
UI never freezes — the Manager triages free text first, off the event loop),
the header is throttle-cached (no disk read per repaint), and the background
events tail invalidates the app on new lines. Non-TTY / NO_COLOR /
``ARGUS_SKILL_NO_TUI=1`` / missing prompt_toolkit fall back to the line REPL.
"""
from __future__ import annotations

import io
import os
import sys
import threading
import time
from contextlib import redirect_stdout
from typing import Any

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# A small, modern palette. Class names are referenced from FormattedText fragments
# (style="class:…") and the global Style attached to the Application.
_STYLE = {
    "status": "bg:#1f2430 #d8dee9",
    "status.brand": "bg:#1f2430 #88c0d0 bold",
    "status.on": "bg:#1f2430 #a3be8c bold",
    "status.off": "bg:#1f2430 #bf616a bold",
    "status.spin": "bg:#1f2430 #ebcb8b",
    "frame.border": "#4c566a",
    "frame.label": "#88c0d0 bold",
    "task.id": "#88c0d0",
    "task.body": "#d8dee9",
    "feed": "#d8dee9",
    "feed.you": "#88c0d0 bold",
    "feed.argus": "#a3be8c",
    "feed.err": "#bf616a bold",
    "feed.block": "#ebcb8b bold",
    "prompt": "#88c0d0 bold",
}


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


def run_manager_tui(mem: Any, chat_state: dict, global_root: Any) -> int:
    """Drive the full-screen TUI. Returns the REPL exit code."""
    from prompt_toolkit import Application
    from prompt_toolkit.application import get_app
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.completion import FuzzyWordCompleter
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.dimension import D
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Frame

    from . import repl as _repl

    life_dir = _repl._life_dir_for(mem)
    feed: list[tuple[str, str]] = []  # (style_class, text)
    spin = {"i": 0, "stage": "idle", "act": "", "cost": chat_state.get("last_cost_usd"),
            "busy": ""}
    hdr = {"ts": 0.0, "daemon": "○ no daemon", "on": False, "pend": 0}
    stop = threading.Event()

    def push(text: str, style: str = "class:feed") -> None:
        for ln in str(text).splitlines() or [""]:
            feed.append((style, ln))
        del feed[:-2000]

    def invalidate() -> None:
        try:
            get_app().invalidate()
        except Exception:  # noqa: BLE001 — not running yet / already exited
            pass

    # ---- header (throttle-cached: no disk read per repaint) -------------
    def _refresh_hdr() -> None:
        now = time.time()
        if now - hdr["ts"] < 1.2:
            return
        hdr["ts"] = now
        try:
            from ..daemon.life_worker import read_daemon_status
            st = read_daemon_status(life_dir)
            hdr["on"] = bool(getattr(st, "alive", False))
            hdr["daemon"] = f"⚡ daemon {st.pid}" if hdr["on"] else "○ no daemon"
        except Exception:  # noqa: BLE001
            hdr["on"], hdr["daemon"] = False, "○ no daemon"
        try:
            hdr["pend"] = len(mem.backlog.pending()) if hasattr(mem, "backlog") else 0
        except Exception:  # noqa: BLE001
            hdr["pend"] = 0

    def header() -> Any:
        _refresh_hdr()
        cost = spin["cost"]
        c = f"${cost:.2f}" if isinstance(cost, (int, float)) else "$0.00"
        moving = spin["stage"] != "idle" or spin["busy"]
        sp = _SPINNER[spin["i"] % len(_SPINNER)] if moving else "●"
        if spin["busy"]:
            activity = spin["busy"]
        elif spin["stage"] == "idle":
            activity = "idle"
        else:
            activity = " ".join(x for x in (spin["stage"], spin["act"]) if x)
        return [
            ("class:status.brand", "  argus  "),
            ("class:status.on" if hdr["on"] else "class:status.off", hdr["daemon"]),
            ("class:status", f"   {hdr['pend']} pending   {c}   "),
            ("class:status.spin", f"{sp} {activity}"),
            ("class:status", "                                                  "),
        ]

    def tasks_text() -> Any:
        out: list[tuple[str, str]] = []
        try:
            items = (mem.backlog.pending() or [])[:16]
        except Exception:  # noqa: BLE001
            items = []
        if not items:
            return [("class:task.body", " (no pending tasks)")]
        for it in items:
            out.append(("class:task.id", f" {it.id[:6]} "))
            out.append(("class:task.body", f"{it.objective[:26]}\n"))
        return out

    def feed_text() -> Any:
        frags: list[tuple[str, str]] = [(s, t + "\n") for s, t in feed[-1000:]]
        # Pin the viewport to the BOTTOM: a FormattedTextControl anchors to the
        # top and clips, so a growing log gets stuck on the first screen. The
        # ``[SetCursorPosition]`` marker fragment tells the Window where the
        # cursor is; the Window scrolls to keep it visible → always tail.
        frags.append(("[SetCursorPosition]", ""))
        return frags

    # ---- background events tail → invalidate on new lines ---------------
    def tail() -> None:
        import json

        from ..apps.cli._follow import _follow_layer_from_event, _format_follow_event
        p = life_dir / "events.jsonl"
        off = 0
        layer = "engineer"
        while not stop.is_set():
            new = False
            try:
                with p.open() as fh:
                    fh.seek(off)
                    chunk = fh.read()
                    off = fh.tell()
            except OSError:
                time.sleep(0.5)
                continue
            for ln in chunk.splitlines():
                try:
                    e = json.loads(ln)
                except Exception:  # noqa: BLE001
                    continue
                t = str(e.get("type") or "")
                layer = _follow_layer_from_event(e, layer)
                if t in ("life.phase.started", "round.start", "loop.start"):
                    spin["stage"] = layer or "engineer"
                elif t == "round.review.started":
                    spin["stage"] = "reviewer"
                elif t == "engineer.progress":
                    # Surface what codex is doing RIGHT NOW: reasoning vs running
                    # a shell command (the event text is the command or the
                    # model's prose). Keeps the status bar honest live.
                    spin["stage"] = layer or spin["stage"]
                    txt = str(e.get("text") or "").strip()
                    low = txt.lower()
                    is_cmd = (txt.startswith(("/bin/bash", "./")) or " -lc " in txt
                              or low.startswith(("bash", "python", "cd ", "rg ", "sed ",
                                                 "find ", "ls ", "cat ", "grep ", "git ",
                                                 "make ", "nvcc", "pytest", "echo ")))
                    spin["act"] = "⚙ 跑命令" if is_cmd else "💭 思考中"
                elif t in ("life.planner.start",):
                    spin["stage"] = "planner"
                    spin["act"] = "💭 规划中"
                elif t in ("life.mission.completed", "loop.done"):
                    spin["stage"] = "idle"
                    spin["act"] = ""
                    spin["cost"] = e.get("cost_usd") or spin["cost"]
                r = _format_follow_event(e, layer)
                if r:
                    push(r)
                    new = True
            spin["i"] += 1
            if new or spin["stage"] != "idle":
                invalidate()
            time.sleep(0.5)
    threading.Thread(target=tail, daemon=True).start()

    # ---- input + dispatch (ALWAYS off the UI thread) -------------------
    kb = KeyBindings()
    history = FileHistory(str(life_dir / ".repl_history"))
    inp = Buffer(history=history, multiline=False,
                 completer=FuzzyWordCompleter([c for c, _ in _repl.SLASH_COMMANDS]))

    def _work(line: str) -> None:
        try:
            if line.startswith("/"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _repl.dispatch_command(line, line, mem, chat_state, global_root,
                                           _plain_theme())
                if buf.getvalue().strip():
                    push(buf.getvalue())
            else:
                # Manager is the FIRST responder: triage chat vs task.
                spin["busy"] = "💭 manager 思考中…"
                invalidate()
                reply = None
                if not chat_state.get("blocked_item_id"):
                    reply = _repl.manager_triage(mem, line, chat_state)
                if reply is not None:
                    push(f"argus ↳ {reply}", "class:feed.argus")
                else:
                    item, alive, pid = _repl.enqueue_mission(mem, line, chat_state)
                    if alive:
                        push(f"queued {item.id} → daemon {pid} executing", "class:feed")
                    else:
                        push(f"queued {item.id} — no daemon yet (start: argus-skill --daemon)",
                             "class:feed.err")
                if chat_state.get("blocked_question"):
                    push(f"❓ 需要你定夺：{chat_state['blocked_question']}（直接回复即可）",
                         "class:feed.block")
        except Exception as exc:  # noqa: BLE001
            push(f"error: {exc}", "class:feed.err")
        finally:
            spin["busy"] = ""
            invalidate()

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
        push(f"› {line}", "class:feed.you")
        threading.Thread(target=_work, args=(line,), daemon=True).start()

    @kb.add("c-c")
    @kb.add("c-d")
    def _(ev) -> None:
        stop.set()
        ev.app.exit(0)

    body = VSplit([
        Frame(Window(FormattedTextControl(tasks_text), wrap_lines=True),
              title="TASKS", width=D(min=20, weight=1)),
        Frame(Window(FormattedTextControl(feed_text), wrap_lines=True),
              title="ACTIVITY", width=D(weight=3)),
    ])
    root = HSplit([
        Window(FormattedTextControl(header), height=1, style="class:status"),
        body,
        VSplit([Window(FormattedTextControl([("class:prompt", " › ")]), width=3),
                Window(BufferControl(inp), height=1)]),
    ])
    app = Application(
        layout=Layout(root, focused_element=inp),
        key_bindings=kb, full_screen=True,
        # mouse_support OFF on purpose: when on, prompt_toolkit captures mouse
        # events and the terminal's native click-drag SELECT/COPY stops working.
        # Off → the operator can select and copy text the normal way.
        mouse_support=False,
        refresh_interval=0.5, style=Style.from_dict(_STYLE),
    )
    # No synthetic auto-greet (it made the user's own "你好" look like a second
    # reply). The manager introduces itself — from its own system prompt — the
    # first time the operator says hello. Open with one neutral, dim ready line.
    push("manager 待命中 — 说你的任务或打个招呼，我先帮你分流。/help 看命令。", "class:feed")
    try:
        app.run()
    finally:
        stop.set()
    return 0


def _plain_theme() -> Any:
    """A no-ANSI theme so captured handler stdout stays clean inside the pane."""
    from ..cli.theme import Theme
    return Theme(enabled=False, width=100)
