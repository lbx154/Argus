"""Full-screen TUI for the argus-skill REPL (single-column, Claude-Code/Codex
CLI style — not a two-pane dashboard).

A prompt_toolkit Application wrapping the existing handlers — business logic
stays in ``repl.py``; this only renders. Layout, top to bottom:

    argus  <daemon>  <pending>  <cost>  <spinner> <activity>   <- one chrome bar
    <current task summary>                                     <- plain, subordinate
                                                                <- blank
    activity feed (events.jsonl, live, full width, scrolls)
                                                                <- blank
    [composing preview]                                        <- only while typing
    [decision needed — choices]                                <- only while blocked
    › input (multi-line; Enter sends, Esc+Enter inserts a newline)
      Enter send · Esc+Enter newline

Deliberately ONE colored chrome row (the status bar) rather than boxed panes:
box-drawing / multiple separators read as a spreadsheet, not a chat. Avoid
lightning-bolt/gear/thought-bubble/question-mark style emoji (Unicode
"ambiguous" East-Asian width) in the single-line status/activity indicators —
mixed with CJK text they desync prompt_toolkit's column math from the
terminal's actual rendering and produce literal character corruption on
repaint. They're fine inside the scrolling feed (each line is independent),
just not in the tightly packed header.

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
import textwrap
from contextlib import redirect_stdout
from typing import Any

from ..cli.live_status import _fmt_elapsed

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _clip_cell(text: str, width: int) -> str:
    """Single-line display clipping for narrow TUI panes."""
    text = " ".join(str(text or "").split())
    if len(text) <= width:
        return text
    return text[: max(1, width - 1)].rstrip() + "…"


def _decision_choices(question: str) -> list[str]:
    """Default operator choices for a blocked mission question."""
    q = str(question or "").lower()
    if any(k in q for k in ("approve", "approval", "budget", "spend", "paid", "cap")):
        return [
            "批准：先跑最小 supervised smoke，预算 cap $1，超过就停；不要跑 full matrix。",
            "批准：跑三 family pilot，总预算 cap $30；先不要跑 full matrix。",
            "不批准：暂不花 API，只补离线 artifact 和风险说明。",
        ]
    return [
        "批准，按 reviewer 推荐的最小安全步骤执行。",
        "不批准，先不要继续执行；给出替代方案。",
        "先暂停，补充说明预算、风险、预计产出后再问我。",
    ]


def _modal_fragments(
    question: str,
    choices: list[str],
    selected: int,
    *,
    width: int = 76,
) -> list[tuple[str, str]]:
    """Render the decision modal as formatted text."""
    q_lines = textwrap.wrap(" ".join(str(question).split()), width=max(30, width - 4))
    frags: list[tuple[str, str]] = [
        ("class:modal.title", " decision needed  "),
        ("class:modal.hint", "↑/↓ choose · Enter send · Esc dismiss\n"),
    ]
    for line in q_lines[:4]:
        frags.append(("class:modal.text", f"{line}\n"))
    frags.append(("class:modal.text", "\n"))
    for i, choice in enumerate(choices[:6]):
        style = "class:modal.selected" if i == selected else "class:modal.option"
        prefix = "›" if i == selected else " "
        frags.append((style, f"{prefix} {_clip_cell(choice, width - 3)}\n"))
    return frags


# A muted, box-light palette. Class names are referenced from FormattedText
# fragments (style="class:…") and the global Style attached to the Application.
_STATUS_BG = "bg:#1b2029"
_STYLE = {
    # The whole top bar shares ONE background so it reads as a single clean
    # strip (no seams between differently-colored fragments, no need to
    # hand-pad trailing whitespace to "clear" the row).
    "status": f"{_STATUS_BG} #9aa7b8",
    "status.brand": f"{_STATUS_BG} #88c0d0 bold",
    "status.on": f"{_STATUS_BG} #a3be8c bold",
    "status.off": f"{_STATUS_BG} #bf616a bold",
    "status.spin": f"{_STATUS_BG} #ebcb8b",
    "status.elapsed": f"{_STATUS_BG} #6f7d91",
    "status.hint": f"{_STATUS_BG} #566274",
    "pane.title": "#6f7d91",
    "pane.title.hot": "#88c0d0 bold",
    "pane.hint": "#566274",
    "task.run": "#ebcb8b bold",
    "task.body": "#d8dee9",
    "feed": "#d8dee9",
    "feed.you": "#88c0d0 bold",
    "feed.argus": "#a3be8c",
    "feed.err": "#bf616a bold",
    "feed.block": "#ebcb8b bold",
    "feed.hint": "#6f7d91",
    "feed.dim": "#566274",
    "modal": "bg:#181c23 #d8dee9",
    "modal.title": "bg:#181c23 #88c0d0 bold",
    "modal.text": "bg:#181c23 #d8dee9",
    "modal.hint": "bg:#181c23 #6f7d91",
    "modal.option": "bg:#181c23 #d8dee9",
    "modal.selected": "bg:#2f3542 #eceff4 bold",
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
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.layout import (
        ConditionalContainer,
        HSplit,
        Layout,
        VSplit,
        Window,
    )
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.dimension import D
    from prompt_toolkit.styles import Style

    from . import repl as _repl

    life_dir = _repl._life_dir_for(mem)
    # Full event text in the ACTIVITY pane (no 220-char clip) — the pane wraps.
    os.environ.setdefault("ARGUS_SKILL_FOLLOW_FULL", "1")
    feed: list[tuple[str, str]] = []  # (style_class, text)
    spin = {"i": 0, "stage": "idle", "act": "", "cost": chat_state.get("last_cost_usd"),
            "busy": "", "work_start": 0.0}
    hdr = {"ts": 0.0, "daemon": "○ no daemon", "on": False, "pend": 0}
    # ACTIVITY scroll: ``off`` = lines scrolled up from the bottom (0 = following
    # the live tail). PageUp pauses follow; PageDown / End resume it.
    view = {"off": 0}
    modal = {
        "question": "",
        "choices": [],
        "selected": 0,
        "item_id": "",
    }
    stop = threading.Event()

    def push(text: str, style: str = "class:feed") -> None:
        lines = str(text).splitlines() or [""]
        feed.extend((style, ln) for ln in lines)
        del feed[:-4000]
        # If the operator is scrolled up reading history, keep their view anchored
        # (don't yank to the bottom) by advancing the offset past the new lines.
        if view["off"] > 0:
            view["off"] += len(lines)

    def invalidate() -> None:
        try:
            get_app().invalidate()
        except Exception:  # noqa: BLE001 — not running yet / already exited
            pass

    def modal_active() -> bool:
        return bool(str(modal.get("question") or "").strip())

    def open_decision_modal(question: str, *, item_id: str = "") -> None:
        question = str(question or "").strip()
        if not question:
            return
        if modal_active() and modal.get("question") == question:
            return
        modal["question"] = question
        modal["choices"] = _decision_choices(question)
        modal["selected"] = 0
        modal["item_id"] = item_id
        push("! decision needed — use ↑/↓ then Enter", "class:feed.block")
        invalidate()

    def close_decision_modal() -> None:
        modal["question"] = ""
        modal["choices"] = []
        modal["selected"] = 0
        modal["item_id"] = ""
        invalidate()

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
            hdr["daemon"] = f"● daemon {st.pid}" if hdr["on"] else "○ no daemon"
        except Exception:  # noqa: BLE001
            hdr["on"], hdr["daemon"] = False, "○ no daemon"
        try:
            hdr["pend"] = len(mem.backlog.pending()) if hasattr(mem, "backlog") else 0
        except Exception:  # noqa: BLE001
            hdr["pend"] = 0

    def _elapsed_str() -> str:
        start = spin.get("work_start") or 0.0
        if not start:
            return ""
        return _fmt_elapsed(time.monotonic() - start)

    def _app_width(default: int = 100) -> int:
        try:
            return get_app().output.get_size().columns
        except Exception:  # noqa: BLE001 — not running yet
            return default

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
        daemon_style = "class:status.on" if hdr["on"] else "class:status.off"
        width = _app_width()
        # Narrow terminals: drop the daemon/queue/cost segment and shorten the
        # activity so the animated spinner + phase never wrap the status row
        # (Gemini CLI's isNarrow<80 collapse). Wide terminals show everything.
        if width < 60:
            frags: list[tuple[str, str]] = [
                ("class:status.brand", " argus "),
                ("class:status.spin", sp),
                ("class:status", f" {_clip_cell(activity, max(8, width - 12))}"),
            ]
        else:
            act_budget = 42 if width >= 100 else max(14, width - 58)
            frags = [
                ("class:status.brand", " argus"),
                ("class:status", "  "),
                (daemon_style, hdr["daemon"]),
                ("class:status", f"  {hdr['pend']} queued  {c}  "),
                ("class:status.spin", sp),
                ("class:status", f" {_clip_cell(activity, act_budget)}"),
            ]
        elapsed = _elapsed_str() if moving else ""
        if elapsed:
            frags.append(("class:status.elapsed", f"  {elapsed}"))
            if spin["busy"] and width >= 80:
                frags.append(("class:status.hint", "  esc to interrupt"))
        return frags

    def task_summary() -> Any:
        try:
            items = list(mem.backlog.all()) if hasattr(mem, "backlog") else []
        except Exception:  # noqa: BLE001
            items = []
        if not items:
            return [("class:feed.hint", " no tasks yet")]
        running = [it for it in items if getattr(it, "status", "") == "running"]
        pending = [it for it in items if getattr(it, "status", "") == "pending"]
        done = [
            it
            for it in items
            if getattr(it, "status", "") in ("done", "failed", "blocked")
        ]
        frags: list[tuple[str, str]] = []
        if running:
            title = str(
                getattr(running[0], "title", "")
                or getattr(running[0], "objective", "")
                or ""
            )
            frags.extend([
                ("class:pane.title.hot", "running "),
                ("class:task.run", "◐ "),
                ("class:task.body", _clip_cell(title, 64)),
            ])
        else:
            frags.append(("class:pane.title", "idle"))
        if pending:
            frags.extend([
                ("class:feed.hint", "  ·  "),
                ("class:pane.title", f"queued {len(pending)}"),
            ])
        if done:
            frags.extend([
                ("class:feed.hint", "  ·  "),
                ("class:pane.title", f"history {len(done)}"),
            ])
        return frags

    def feed_text() -> Any:
        lines = feed[-4000:]
        n = len(lines)
        frags: list[tuple[str, str]] = [
            ("class:pane.title.hot", "activity"),
            ("class:pane.hint", "  PgUp/PgDn · End\n"),
        ]
        # The cursor marker drives the Window's scroll: at the last line when
        # following (off=0 → bottom), or ``off`` lines up when paused.
        cursor_at = max(0, n - 1 - max(0, view["off"]))
        for i, (s, t) in enumerate(lines):
            if i == cursor_at:
                frags.append(("[SetCursorPosition]", ""))
            frags.append((s, t + "\n"))
        if not lines:
            frags.append(("[SetCursorPosition]", ""))
            frags.append(("class:feed.hint", "  no events yet — say a task or run /help\n"))
        return frags

    # ---- background events tail → invalidate on new lines ---------------
    def tail() -> None:
        import json

        from ..apps.cli._follow import (
            _follow_layer_from_event,
            _format_follow_event,
            _read_backlog_rows,
            _select_backlog_row_by_id,
        )
        p = life_dir / "events.jsonl"
        backlog_path = life_dir / "backlog.jsonl"
        off = 0
        layer = "engineer"
        current_mission: dict[str, str] = {
            "item_id": "",
            "title": "",
            "objective": "",
            "status": "",
        }
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
                if t in {"life.mission.started", "life.mission.completed"}:
                    item_id = str(e.get("item_id") or current_mission.get("item_id") or "")
                    title = str(e.get("title") or current_mission.get("title") or "")
                    objective = str(
                        e.get("objective") or current_mission.get("objective") or ""
                    )
                    if item_id:
                        row = _select_backlog_row_by_id(
                            _read_backlog_rows(backlog_path),
                            item_id,
                        )
                        if row is not None:
                            title = str(row.get("title") or title)
                            objective = str(row.get("objective") or objective)
                            status = str(row.get("status") or "")
                        else:
                            status = current_mission.get("status", "")
                    else:
                        status = current_mission.get("status", "")
                    current_mission = {
                        "item_id": item_id,
                        "title": title,
                        "objective": objective,
                        "status": status,
                    }
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
                    spin["act"] = "跑命令" if is_cmd else "思考中"
                elif t in ("life.planner.start",):
                    spin["stage"] = "planner"
                    spin["act"] = "规划中"
                elif t in ("life.mission.completed", "loop.done"):
                    spin["stage"] = "idle"
                    spin["act"] = ""
                    spin["cost"] = e.get("cost_usd") or spin["cost"]
                if t == "round.review.completed" and str(e.get("status") or "") == "blocked":
                    q = str(e.get("operator_question") or "").strip()
                    active_status = str(current_mission.get("status") or "")
                    if q and active_status in {"running", "blocked"}:
                        item_id = str(current_mission.get("item_id") or "")
                        if item_id:
                            chat_state["blocked_item_id"] = item_id
                        if current_mission.get("objective"):
                            chat_state["last_objective"] = current_mission["objective"]
                        chat_state["blocked_question"] = q
                        open_decision_modal(q, item_id=item_id)
                r = _format_follow_event(e, layer, mission_context=current_mission)
                if r:
                    push(r)
                    new = True
            if new or spin["stage"] != "idle":
                invalidate()
            time.sleep(0.5)
    threading.Thread(target=tail, daemon=True).start()

    # Dedicated spinner thread: advance the braille frame every 80ms while the
    # agent is working (Claude Code / Codex cadence), and drive the header's
    # elapsed timer off the rising/falling edge of "moving". Kept separate from
    # the 0.5s event tail so the spinner stays smooth even when no new events
    # arrive — the whole point of an animated "Working…" indicator.
    def _spinner() -> None:
        was_moving = False
        while not stop.is_set():
            moving = spin["stage"] != "idle" or bool(spin["busy"])
            if moving and not was_moving:
                spin["work_start"] = time.monotonic()
            elif not moving and was_moving:
                spin["work_start"] = 0.0
            was_moving = moving
            if moving:
                spin["i"] += 1
                invalidate()
            stop.wait(0.08)
    threading.Thread(target=_spinner, daemon=True).start()

    # ---- input + dispatch (ALWAYS off the UI thread) -------------------
    kb = KeyBindings()
    modal_filter = Condition(modal_active)
    history = FileHistory(str(life_dir / ".repl_history"))
    inp = Buffer(
        history=history,
        multiline=True,
        read_only=modal_filter,
        completer=FuzzyWordCompleter([c for c, _ in _repl.SLASH_COMMANDS]),
    )
    input_has_text = Condition(lambda: bool(inp.text))

    def input_preview() -> Any:
        text = inp.text.rstrip("\n")
        if not text:
            return []
        width = 94
        lines: list[str] = []
        for raw in text.splitlines() or [""]:
            wrapped = textwrap.wrap(raw, width=width) or [""]
            lines.extend(wrapped)
        shown = lines[-5:]
        frags: list[tuple[str, str]] = [("class:feed.hint", " composing\n")]
        for line in shown:
            frags.append(("class:feed", f"  {line}\n"))
        return frags

    def _work(line: str) -> None:
        try:
            if line.startswith("/") or _repl._is_argus_cli_invocation(line):
                buf = io.StringIO()
                plain_theme = _plain_theme()
                old_theme = chat_state.get("theme")
                with redirect_stdout(buf):
                    chat_state["theme"] = plain_theme
                    try:
                        _repl.dispatch_command(
                            line, line, mem, chat_state, global_root, plain_theme
                        )
                    finally:
                        if old_theme is None:
                            chat_state.pop("theme", None)
                        else:
                            chat_state["theme"] = old_theme
                if buf.getvalue().strip():
                    push(buf.getvalue())
            else:
                # Manager is the FIRST responder: triage chat vs task.
                spin["busy"] = "manager 思考中…"
                invalidate()
                reply = None
                if not chat_state.get("blocked_item_id"):
                    reply = _repl.manager_triage(mem, line, chat_state)
                if reply is not None:
                    push(f"argus ↳ {reply}", "class:feed.argus")
                else:
                    item, alive, pid = _repl.enqueue_mission(mem, line, chat_state)
                    if alive:
                        push(f"queued {item.id} → daemon {pid} executing", "class:feed.dim")
                    else:
                        push(_repl._no_executor_notice(item.id, None), "class:feed.err")
                if chat_state.get("blocked_question"):
                    push(f"! 需要你定夺：{chat_state['blocked_question']}（直接回复即可）",
                         "class:feed.block")
        except Exception as exc:  # noqa: BLE001
            push(f"error: {exc}", "class:feed.err")
        finally:
            spin["busy"] = ""
            invalidate()

    def _submit_line(line: str) -> None:
        push(f"› {line}", "class:feed.you")
        threading.Thread(target=_work, args=(line,), daemon=True).start()

    def _submit_modal_choice() -> None:
        choices = modal.get("choices") or []
        if not choices:
            close_decision_modal()
            return
        try:
            selected = int(modal.get("selected") or 0)
        except (TypeError, ValueError):
            selected = 0
        selected = max(0, min(selected, len(choices) - 1))
        answer = str(choices[selected])
        close_decision_modal()
        _submit_line(answer)

    @kb.add("up", filter=modal_filter)
    @kb.add("k", filter=modal_filter)
    def _(ev) -> None:
        choices = modal.get("choices") or []
        if choices:
            modal["selected"] = (int(modal.get("selected") or 0) - 1) % len(choices)
            invalidate()

    @kb.add("down", filter=modal_filter)
    @kb.add("j", filter=modal_filter)
    def _(ev) -> None:
        choices = modal.get("choices") or []
        if choices:
            modal["selected"] = (int(modal.get("selected") or 0) + 1) % len(choices)
            invalidate()

    @kb.add("escape", filter=modal_filter)
    def _(ev) -> None:
        close_decision_modal()

    @kb.add("<any>", filter=modal_filter)
    def _(ev) -> None:
        # While the decision sheet is open, ignore stray typing so it doesn't
        # accumulate invisibly in the prompt buffer behind the modal.
        return

    @kb.add("enter")
    def _(ev) -> None:
        if modal_active():
            _submit_modal_choice()
            return
        line = inp.text.strip()
        inp.reset()
        if not line:
            return
        if line in ("/exit", "/quit", ":q"):
            stop.set()
            ev.app.exit(0)
            return
        _submit_line(line)

    @kb.add("escape", "enter")
    @kb.add("c-o")
    def _(ev) -> None:
        """Insert a newline in the prompt (Enter submits)."""
        inp.insert_text("\n")

    @kb.add("c-c")
    @kb.add("c-d")
    def _(ev) -> None:
        stop.set()
        ev.app.exit(0)

    # ACTIVITY scroll: PageUp pauses the live tail and scrolls back through
    # history; PageDown scrolls toward the bottom; End jumps back to following.
    @kb.add("pageup")
    def _(ev) -> None:
        view["off"] = min(view["off"] + 10, max(0, len(feed) - 1))

    @kb.add("pagedown")
    def _(ev) -> None:
        view["off"] = max(view["off"] - 10, 0)

    @kb.add("end")
    @kb.add("c-e")
    def _(ev) -> None:
        view["off"] = 0  # resume following the live tail

    input_row = VSplit([
        Window(FormattedTextControl([("class:prompt", " › ")]), width=3),
        Window(BufferControl(inp), height=D(min=1, max=5), wrap_lines=True),
    ])
    modal_window = Window(
        FormattedTextControl(
            lambda: _modal_fragments(
                str(modal.get("question") or ""),
                list(modal.get("choices") or []),
                int(modal.get("selected") or 0),
            )
        ),
        height=10,
        style="class:modal",
    )
    root = HSplit([
        Window(FormattedTextControl(header), height=1, style="class:status"),
        Window(FormattedTextControl(task_summary), height=1),
        Window(height=1),
        Window(FormattedTextControl(feed_text), wrap_lines=True),
        Window(height=1),
        ConditionalContainer(
            Window(FormattedTextControl(input_preview), height=D(min=1, max=6)),
            filter=input_has_text,
        ),
        ConditionalContainer(
            modal_window,
            filter=modal_filter,
        ),
        input_row,
        Window(
            FormattedTextControl([("class:feed.hint", "   Enter send · Esc+Enter newline")]),
            height=1,
        ),
    ])
    app = Application(
        layout=Layout(root, focused_element=inp),
        key_bindings=kb, full_screen=True,
        # mouse_support OFF on purpose: when on, prompt_toolkit captures ALL
        # mouse events (including the scroll wheel), which disables the
        # terminal's own click-drag SELECT/COPY. Copy/paste of paths, task
        # ids, and error text is a constant workflow need in this cockpit, so
        # it wins over wheel-scroll — use PageUp/PageDown/End to scroll the
        # activity feed instead.
        mouse_support=False,
        refresh_interval=0.5, style=Style.from_dict(_STYLE),
    )
    # No synthetic ready line in the feed: empty-state hints are rendered by
    # feed_text(), and real activity should be only user/agent/system events.
    try:
        app.run()
    finally:
        stop.set()
    return 0


def _plain_theme() -> Any:
    """A no-ANSI theme so captured handler stdout stays clean inside the pane."""
    from ..cli.theme import Theme
    return Theme(enabled=False, width=100)
