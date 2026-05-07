"""Life-mode REPL + runner adapters for ``argus-skill chat --life``.

This module owns everything the lifetime-agent interactive loop needs:

- ``run_life_chat_loop``       — public entry point invoked from chat_app
                                  when ``--life`` is set. The single
                                  interactive surface for life mode.
- ``run_life_supervisor``      — non-interactive driver used by
                                  ``argus-skill life run``. Same engine,
                                  same sink, no REPL.
- ``LifeStderrSink``           — chat-style event renderer + verbose/quiet
                                  filter (shared with telegram.notifier).
- ``build_life_runner``        — factory for memory / codex backends.

Why split out of life_app.py: previously life had its own REPL with a
duplicated banner / paste / theme stack. Per the merge plan, the
interactive surface lives next to ``argus-skill chat`` (this file is
imported by chat_app on demand), while ``argus-skill life`` retains
only its non-interactive subcommands and delegates the runner /
supervisor wiring back here. One REPL, one renderer, one help screen.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.ports import EventSink
from ..life import BacklogItem, JournalEntry, LifeMemory
from ..life.memory import default_life_dir
from ..life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)


# ---------------------------------------------------------------------------
# Sink (event rendering)
# ---------------------------------------------------------------------------

class LifeStderrSink:
    """Forward events to stderr using chat's renderer + event filters.

    Same renderer (``render_event_for_terminal``) and same user-facing
    vs internal split (``telegram.notifier._USER_FACING_EVENTS``)
    ``argus-skill chat`` uses, so output looks identical.

    Filter rules:
    - ``quiet=True``  → drop everything (used by ``--quiet`` batch runs).
    - ``verbose=True`` → show user-facing AND internal events.
    - ``verbose=False`` → user-facing only (matches chat default).
    """

    def __init__(self, *, quiet: bool, verbose: bool = False) -> None:
        self.quiet = quiet
        self.verbose = verbose
        try:
            from ..cli import default_theme, render_event_for_terminal
            self._render = render_event_for_terminal
            self._theme = default_theme()
        except Exception:  # noqa: BLE001
            self._render = None
            self._theme = None
        try:
            from ..telegram.notifier import _USER_FACING_EVENTS, _VERBOSE_EVENTS
            self._user_facing = set(_USER_FACING_EVENTS)
            self._verbose_set = set(_VERBOSE_EVENTS)
        except Exception:  # noqa: BLE001
            self._user_facing = set()
            self._verbose_set = set()

    def _allowed(self, event_type: str) -> bool:
        if not self._user_facing:
            return True
        if self.verbose:
            return event_type in self._verbose_set or event_type not in self._user_facing
        return event_type in self._user_facing

    def handle_event(self, event: dict[str, Any]) -> None:
        if self.quiet:
            return
        et = str(event.get("type", ""))
        if not self._allowed(et):
            return
        if self._render is not None:
            try:
                line = self._render(event, theme=self._theme)
                sys.stderr.write(line + "\n")
                sys.stderr.flush()
                return
            except Exception:  # noqa: BLE001
                pass
        text = event.get("text") or event.get("title") or ""
        sys.stderr.write(f"[{et}] {text}\n")
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# Runner adapters
# ---------------------------------------------------------------------------

@dataclass
class _Outcome:
    """Duck-typed outcome the supervisor reads via ``getattr``."""
    success: bool
    status: str
    stop_reason: str = ""
    rounds: int = 1
    matched_skill_name: str | None = None
    skill_distilled: bool = False
    had_follow_up: bool = False


class _MemoryRunner:
    """Deterministic in-process runner for CI / smoke tests.

    Emits a couple of synthetic events with non-zero token counts so
    the daily budget logic is testable, then returns success.
    """

    def execute(
        self,
        *,
        objective: str,
        sink: EventSink,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
    ) -> _Outcome:
        sink.handle_event({
            "type": "round.main.completed",
            "input_tokens": 800,
            "output_tokens": 200,
        })
        sink.handle_event({
            "type": "round.review.completed",
            "input_tokens": 100,
            "output_tokens": 50,
        })
        sink.handle_event({
            "type": "life.adapter.memory",
            "text": f"(memory backend) acknowledged objective: {objective[:80]}",
        })
        return _Outcome(success=True, status="success", rounds=1)


class _CodexSkillLoopRunner:
    """Runs each mission through a fresh ``SkillLoop`` (codex backend).

    Bypasses the ``ARGUS_SKILL_BACKEND`` env var: when life mode
    selects ``codex`` that's the user's explicit ask, so we always
    construct a real ``CodexRunnerBackend``. This was a real bug —
    previously the backend silently fell back to memory when the env
    var was unset, while the UI happily printed ``backend: codex``.
    """

    def __init__(self, args: argparse.Namespace) -> None:
        from ..loop import SkillLoop, SkillLoopConfig

        self._SkillLoop = SkillLoop
        self._SkillLoopConfig = SkillLoopConfig
        try:
            from ..adapters.codex_backend import build_codex_backend_from_env
        except ImportError as exc:  # pragma: no cover — depends on optional install
            raise SystemExit(
                f"Codex backend requested but ArgusBot is unavailable: {exc}.\n"
                "Install it (`pip install -e /path/to/ArgusBot`) or use "
                "/backend memory for the in-process stub."
            ) from exc
        self._backend = build_codex_backend_from_env()
        self._args = args

    def execute(
        self,
        *,
        objective: str,
        sink: EventSink,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
    ) -> _Outcome:
        args = self._args
        config = self._SkillLoopConfig(
            scientist_model=args.scientist_model,
            engineer_model=args.engineer_model,
            reviewer_model=args.reviewer_model,
            max_rounds=args.max_rounds,
            check_commands=[],
            skill_writeback=True,
            distill_on_miss=True,
        )
        loop = self._SkillLoop(
            skills_dir=Path(args.skills_dir),
            scientist_runner=self._backend,
            engineer_runner=self._backend,
            reviewer_runner=self._backend,
            config=config,
            on_event=sink.handle_event,
        )
        full_task = objective
        if prelude_context:
            full_task = f"{prelude_context}\n---\n## Live objective\n{objective}"
        workdir = (
            Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        )
        outcome = loop.run(full_task, workdir=workdir)
        return _Outcome(
            success=outcome.successful,
            status=outcome.status,
            stop_reason=outcome.reason or "",
            rounds=outcome.round_count,
            matched_skill_name=outcome.skill_used,
            skill_distilled=outcome.skill_distilled,
        )


def build_life_runner(args: argparse.Namespace):
    """Return a ``_MissionRunner``-shaped adapter for the requested backend."""
    if args.backend == "memory":
        return _MemoryRunner()
    if args.backend == "codex":
        return _CodexSkillLoopRunner(args)
    raise SystemExit(f"unknown backend: {args.backend}")


# ---------------------------------------------------------------------------
# Supervisor driver (used by both `life run` and chat-mode free text)
# ---------------------------------------------------------------------------

def run_life_supervisor(
    *,
    mem: LifeMemory,
    runner: Any,
    engineer_model: str,
    reviewer_model: str,
    once: bool,
    max_missions: int,
    per_mission_cap_usd: float,
    daily_cap_usd: float,
    quiet: bool,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run ``LifeSupervisor`` with proper signal-handler save/restore.

    Restoring previous SIGINT/SIGTERM handlers on exit means the chat
    REPL keeps its Ctrl-C semantics after a /run finishes.
    """
    stop_event = threading.Event()

    def _on_signal(signum: int, frame: Any) -> None:  # noqa: ANN401
        print(f"\nlife: received signal {signum}, requesting stop", file=sys.stderr)
        stop_event.set()

    prev_int = signal.getsignal(signal.SIGINT)
    prev_term = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        sink = LifeStderrSink(quiet=quiet, verbose=verbose)
        cfg = LifeSupervisorConfig(
            budget=LifeBudget(
                per_mission_cap_usd=per_mission_cap_usd,
                daily_cap_usd=daily_cap_usd,
                max_missions=1 if once else max_missions,
            ),
            poll_interval_seconds=2.0,
            stop_event=stop_event,
        )
        sup = LifeSupervisor(
            memory=mem,
            runner=runner,
            sink=sink,
            config=cfg,
            engineer_model=engineer_model,
            reviewer_model=reviewer_model,
        )
        return sup.run()
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)


def _invoke_supervisor(
    *,
    mem: LifeMemory,
    backend: str,
    once: bool,
    max_missions: int,
    per_mission_cap_usd: float,
    daily_cap_usd: float,
    quiet: bool,
    verbose: bool = False,
) -> dict[str, Any]:
    ns = argparse.Namespace()
    ns.backend = backend
    ns.engineer_model = os.environ.get("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini")
    ns.reviewer_model = os.environ.get("ARGUS_SKILL_REVIEWER_MODEL", "gpt-5.4")
    ns.scientist_model = os.environ.get("ARGUS_SKILL_SCIENTIST_MODEL", "gpt-5.4")
    ns.skills_dir = os.environ.get("ARGUS_SKILL_SKILLS_DIR", "skills")
    ns.workdir = os.environ.get("ARGUS_SKILL_WORKDIR")
    ns.max_rounds = 3

    runner = build_life_runner(ns)
    return run_life_supervisor(
        mem=mem,
        runner=runner,
        engineer_model=ns.engineer_model,
        reviewer_model=ns.reviewer_model,
        once=once,
        max_missions=max_missions,
        per_mission_cap_usd=per_mission_cap_usd,
        daily_cap_usd=daily_cap_usd,
        quiet=quiet,
        verbose=verbose,
    )


# ---------------------------------------------------------------------------
# Slash-command helpers (in-process; mirror the public CLI subcommands)
# ---------------------------------------------------------------------------

def _add_only(mem: LifeMemory, text: str) -> BacklogItem:
    text = text.strip()
    title = text.splitlines()[0][:60].strip() or "(untitled)"
    item = mem.backlog.add(BacklogItem.new(
        title=title,
        objective=text,
        priority=100,
        max_cost_usd=1.0,
        tags=[],
    ))
    print(
        f"added {item.id}: {item.title}  "
        f"(priority={item.priority}, max_cost=${item.max_cost_usd:.2f})"
    )
    return item


def _backend_cmd(tokens: list[str], chat_state: dict[str, Any]) -> None:
    if not tokens:
        print(f"backend: {chat_state['backend']}")
        return
    new = tokens[0].lower()
    if new not in ("codex", "memory"):
        print(f"unknown backend: {new}  (codex|memory)")
        return
    chat_state["backend"] = new
    print(f"backend set to {new}")


def _identity_cmd(mem: LifeMemory, tokens: list[str], rest_text: str) -> None:
    if not tokens:
        text = mem.identity.read().strip()
        if not text:
            print("(identity empty — try /identity edit)")
        else:
            print(text)
        return
    sub = tokens[0].lower()
    if sub == "edit":
        print("Enter new identity card. End with a single '.' on its own line:")
        lines: list[str] = []
        while True:
            try:
                ln = input("> ")
            except (EOFError, KeyboardInterrupt):
                print("\n(aborted, identity unchanged)")
                return
            if ln.strip() == ".":
                break
            lines.append(ln)
        new_text = "\n".join(lines).strip() + "\n"
        mem.identity.path.write_text(new_text, encoding="utf-8")
        print(f"identity card updated ({len(lines)} lines)")
        return
    if sub == "set":
        body = rest_text[len("set"):].lstrip() if rest_text.lower().startswith("set") else ""
        if not body:
            print("usage: /identity set <text>")
            return
        mem.identity.path.write_text(body.rstrip() + "\n", encoding="utf-8")
        print("identity card updated")
        return
    print(f"unknown /identity subcommand: {sub}")


def _backlog_list_cmd(mem: LifeMemory, *, include_all: bool) -> None:
    items = mem.backlog.all() if include_all else [
        i for i in mem.backlog.all() if i.status == "pending"
    ]
    if not items:
        print("(backlog is empty)")
        return
    for it in items:
        print(
            f"  {it.status:<8}  {it.id}  "
            f"p={it.priority:<4}  cap=${it.max_cost_usd:.2f}  "
            f"{it.title}"
        )


def _status_change_cmd(mem: LifeMemory, cmd: str, item_id: str) -> None:
    if cmd == "/done":
        ok = mem.backlog.mark_done(item_id) is not None
    elif cmd == "/skip":
        ok = mem.backlog.update(item_id, status="skipped") is not None
    else:  # /rm
        ok = mem.backlog.remove(item_id)
    print(f"{cmd[1:]}: {item_id}  {'ok' if ok else '(not found)'}")


def _journal_tail_cmd(mem: LifeMemory, n: int) -> None:
    entries = mem.journal.tail(n)
    if not entries:
        print("(journal is empty)")
        return
    for e in entries:
        ts = e.created_at if isinstance(e.created_at, str) else str(e.created_at)
        print(f"  [{ts}] {e.kind:<14} {e.title}")
        if e.summary:
            print(f"      {e.summary}")


def _free_text_cmd(
    mem: LifeMemory,
    text: str,
    chat_state: dict[str, Any],
) -> None:
    """Free-text input: enqueue + run immediately on the current backend."""
    _add_only(mem, text)
    theme = chat_state.get("theme")
    msg = f"running on backend={chat_state['backend']} (Ctrl-C to stop)..."
    print(theme.gray(msg) if theme else msg)
    _invoke_supervisor(
        mem=mem,
        backend=chat_state["backend"],
        once=True,
        max_missions=1,
        per_mission_cap_usd=1.0,
        daily_cap_usd=5.0,
        quiet=False,
        verbose=bool(chat_state.get("verbose")),
    )


def _run_cmd(
    mem: LifeMemory,
    opts: list[str],
    chat_state: dict[str, Any],
) -> None:
    p = argparse.ArgumentParser(prog="/run", add_help=False)
    p.add_argument("--once", action="store_true")
    p.add_argument(
        "--backend",
        choices=("memory", "codex"),
        default=chat_state["backend"],
    )
    p.add_argument("--max-missions", type=int, default=3)
    p.add_argument("--per-mission-cap-usd", type=float, default=1.0)
    p.add_argument("--daily-cap-usd", type=float, default=5.0)
    p.add_argument("--quiet", action="store_true")
    try:
        run_args = p.parse_args(opts)
    except SystemExit:
        return

    print(
        f"/run: backend={run_args.backend}  "
        f"max_missions={'1 (once)' if run_args.once else run_args.max_missions}  "
        f"per_mission_cap=${run_args.per_mission_cap_usd:.2f}  "
        f"daily_cap=${run_args.daily_cap_usd:.2f}"
    )
    print("       (foreground; Ctrl-C requests graceful stop)")

    summary = _invoke_supervisor(
        mem=mem,
        backend=run_args.backend,
        once=run_args.once,
        max_missions=run_args.max_missions,
        per_mission_cap_usd=run_args.per_mission_cap_usd,
        daily_cap_usd=run_args.daily_cap_usd,
        quiet=run_args.quiet,
        verbose=bool(chat_state.get("verbose")),
    )
    print("\n--- /run summary ---")
    print(json.dumps(summary, indent=2, default=str))


def _status_cmd(mem: LifeMemory) -> None:
    """Lightweight status print (mirrors `argus-skill life status` output)."""
    identity = mem.identity.read().strip()
    if identity:
        first = identity.splitlines()[0][:80]
        print(f"identity: {first}{'…' if len(identity) > 80 else ''}")
    else:
        print("identity: (empty)")
    pending = mem.backlog.pending()
    print(f"backlog : {len(pending)} pending  "
          f"({len(mem.backlog.all())} total)")
    for it in pending[:5]:
        print(f"  - {it.id} (p={it.priority}): {it.title}")
    if len(pending) > 5:
        print(f"  … {len(pending) - 5} more")
    last = mem.journal.tail(3)
    if last:
        print("recent journal:")
        for e in last:
            print(f"  [{e.created_at}] {e.kind} — {e.title}")


# ---------------------------------------------------------------------------
# Help screen
# ---------------------------------------------------------------------------

def _render_help(theme) -> str:  # noqa: ANN001
    rows: list[tuple[str, str]] = [
        ("/help", "show this help"),
        ("/status", "summary of identity, backlog, recent journal"),
        ("/identity [edit|set …]", "view or update the identity card"),
        ("/backlog [all]", "list pending (or all) items"),
        ("/add <text>", "enqueue a mission WITHOUT running it"),
        ("/done|/skip|/rm <id>", "change item status"),
        ("/journal [N]", "tail last N journal entries (default 10)"),
        ("/note <text>", "append a manual journal note"),
        ("/run [opts]", "drain the backlog (foreground; Ctrl-C stops)"),
        ("/backend [codex|memory]", "show / set free-text default backend"),
        ("/verbose", "show internal lifecycle events"),
        ("/quiet", "hide internal events (default)"),
        ("/exit  /quit  :q", "leave the REPL (Ctrl-D also works)"),
    ]
    width = max(len(k) for k, _ in rows)
    out: list[str] = []
    out.append(theme.bold("argus-skill chat --life") + theme.gray("  — interactive lifetime-agent REPL"))
    out.append("")
    out.append(theme.gray("Slash commands:"))
    for key, desc in rows:
        out.append(f"  {theme.cyan(key.ljust(width))}  {theme.gray(desc)}")
    out.append("")
    out.append(theme.gray(
        "Free text (no leading '/') is appended to the backlog AND runs immediately"
    ))
    out.append(theme.gray(
        "on the current default backend.  Use /add to enqueue without running."
    ))
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Public entry point — invoked by chat_app when --life is set
# ---------------------------------------------------------------------------

def run_life_chat_loop(args: argparse.Namespace) -> int:
    """Drive the life-mode REPL.

    Shares ``read_pasted_message`` (paste handling), ``Theme.auto``
    (color), ``render_startup_banner`` (logo + tagline), and the
    user-facing/internal event filter with ``argus-skill chat``. Slash
    commands dispatch in-process — no daemon, no jsonl bus.
    """
    import readline  # noqa: F401 — enables line-editing for input()
    from ._input_helpers import read_pasted_message
    from ..cli.theme import Theme
    from ..cli.branding import render_logo, TAGLINE
    from .. import __version__ as _argus_version

    life_dir_arg = getattr(args, "life_dir", None)
    root = Path(life_dir_arg).expanduser() if life_dir_arg else default_life_dir()
    mem = LifeMemory.open(root)
    state = mem.init()
    created = [k for k, v in state.items() if v]

    theme = Theme.auto(force=getattr(args, "color", None))

    # Verbose default is on for life mode: this REPL has no separate
    # progress UI, so seeing internal events (round.start, match.info,
    # skill.writeback, …) is what tells the user the agent is actually
    # working. CLI flags --verbose/--quiet still win.
    if getattr(args, "verbose", None) is None:
        initial_verbose = True
    else:
        initial_verbose = bool(args.verbose)

    backend_default = os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex")
    chat_state: dict[str, Any] = {
        "backend": backend_default,
        "verbose": initial_verbose,
        "theme": theme,
    }

    # ── Banner ─────────────────────────────────────────────────────
    print()
    print(render_logo(theme=theme))
    print()
    print("  " + theme.italic(theme.gray(TAGLINE))
          + "  " + theme.dim(f"v{_argus_version}"))
    print()
    rule = theme.dim("─" * min(theme.width - 2, 60))
    print("  " + rule)

    arrow = theme.dim("→")
    label = lambda s: theme.gray(f"{s:<10}")  # noqa: E731
    verbose_text = (theme.bold(theme.yellow("on"))
                    if initial_verbose
                    else theme.bold_green("off"))
    rows = [
        ("mode",    f"{theme.bold('life')}    " + theme.dim("in-process · no daemon")),
        ("backend", f"{theme.bold(backend_default)}   " + theme.dim("(/backend memory|codex)")),
        ("backlog", f"{theme.bold(str(len(mem.backlog.pending())))} "
                    + theme.gray("pending")),
        ("verbose", f"{verbose_text}      " + theme.dim("(/verbose · /quiet)")),
        ("state",   theme.cyan(str(mem.root))),
    ]
    for k, v in rows:
        print(f"  {label(k)} {arrow} {v}")
    if created:
        print(f"  {label('init')} {arrow} " + theme.dim("created ")
              + theme.cyan(", ".join(created)))
    print("  " + rule)
    print()
    print("  " + theme.gray("free text runs immediately on the backend  ·  ")
          + theme.cyan("/help") + theme.gray(" for commands  ·  ")
          + theme.cyan("/exit") + theme.gray(" or Ctrl-D to leave"))
    print()

    prompt = theme.bold(theme.cyan("argus")) + theme.dim(" › ")

    while True:
        try:
            raw = read_pasted_message(prompt)
        except KeyboardInterrupt:
            print()
            continue
        if raw is None:
            print()
            print(theme.gray("bye."))
            return 0
        line = raw.strip()
        if not line:
            continue

        if line in ("/quit", "/exit", ":q", ":quit"):
            print(theme.gray("bye."))
            return 0

        if not line.startswith("/"):
            _free_text_cmd(mem, raw, chat_state)
            continue

        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            print(theme.red(f"parse error: {exc}"))
            continue
        cmd = tokens[0].lower()
        rest = tokens[1:]
        rest_text = line[len(tokens[0]):].lstrip()

        if cmd in ("/help", "/commands"):
            sys.stdout.write(_render_help(theme))
            sys.stdout.flush()
            continue
        if cmd == "/status":
            _status_cmd(mem)
            continue
        if cmd == "/identity":
            _identity_cmd(mem, rest, rest_text)
            continue
        if cmd == "/backlog":
            include_all = bool(rest) and rest[0].lower() == "all"
            _backlog_list_cmd(mem, include_all=include_all)
            continue
        if cmd == "/add":
            if not rest_text:
                print(theme.gray("usage: /add <objective text>"))
                continue
            _add_only(mem, rest_text)
            continue
        if cmd in ("/done", "/skip", "/rm"):
            if not rest:
                print(theme.gray(f"usage: {cmd} <item_id>"))
                continue
            _status_change_cmd(mem, cmd, rest[0])
            continue
        if cmd == "/journal":
            n = 10
            if rest:
                try:
                    n = int(rest[0])
                except ValueError:
                    print(theme.gray(f"usage: /journal [N]  (got: {rest[0]!r})"))
                    continue
            _journal_tail_cmd(mem, n)
            continue
        if cmd == "/note":
            if not rest_text:
                print(theme.gray("usage: /note <text>"))
                continue
            entry = JournalEntry.new(
                kind="user_note",
                title="manual note",
                summary=rest_text,
                tags=[],
            )
            mem.journal.append(entry)
            print(theme.gray(f"note appended (id={entry.id})"))
            continue
        if cmd == "/backend":
            _backend_cmd(rest, chat_state)
            continue
        if cmd == "/verbose":
            chat_state["verbose"] = True
            print(theme.gray("verbose: on  (showing internal events)"))
            continue
        if cmd == "/quiet":
            chat_state["verbose"] = False
            print(theme.gray("verbose: off  (user-facing events only)"))
            continue
        if cmd == "/run":
            _run_cmd(mem, rest, chat_state)
            continue
        print(theme.gray(f"unknown command: {cmd}  (try /help)"))


__all__ = [
    "run_life_chat_loop",
    "run_life_supervisor",
    "build_life_runner",
    "LifeStderrSink",
]
