"""``argus-skill life`` subcommands — lifetime-agent CLI.

Three categories:

1. **Data management** (``init`` / ``status`` / ``backlog`` / ``journal``)
   — pure file operations. No LLM. Always usable.
2. **Run** (``life run``) — drive ``LifeSupervisor`` to clear the
   backlog, with budget caps and journal updates. Two backends:
     * ``--backend memory`` (default) — deterministic in-process stub
       via ``MemoryBackend``. Safe for shape-tests and CI.
     * ``--backend codex`` — real ArgusBot CodexRunner; requires
       OPENAI_API_KEY and consumes tokens.
3. **Quick actions** (``life add``, ``life next``) — convenience
   shortcuts for the common interactive flow.

Design notes:

- The supervisor itself does not require knowing which backend is in
  play; the CLI just constructs a ``_MissionRunner``-shaped adapter
  and hands it to the supervisor.
- We keep the codex code path lazy-imported. The data commands work
  even when ArgusBot is missing.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.ports import EventSink
from ..life import (
    Backlog,
    BacklogItem,
    IdentityCard,
    Journal,
    JournalEntry,
    LifeMemory,
)
from ..life.memory import default_life_dir
from ..life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subcommand wiring
# ---------------------------------------------------------------------------

def add_life_subcommand(sub) -> None:  # noqa: ANN001 — argparse subparsers obj
    """Attach ``life`` and its sub-subcommands to the top-level parser."""
    life_p = sub.add_parser(
        "life",
        help="lifetime-agent: persistent memory + autonomous self-iteration",
    )
    life_p.add_argument(
        "--life-dir",
        default=None,
        help="override life root (default: $ARGUS_SKILL_LIFE_DIR or ~/.argus-skill/life)",
    )
    life_sub = life_p.add_subparsers(dest="life_cmd", required=True)

    # init
    life_sub.add_parser(
        "init",
        help="seed identity.md / journal.jsonl / backlog.jsonl in the life dir",
    )

    # status
    life_sub.add_parser(
        "status",
        help="render a human summary of identity, backlog, recent journal",
    )

    # backlog group
    backlog_p = life_sub.add_parser("backlog", help="manage the autonomous backlog")
    backlog_sub = backlog_p.add_subparsers(dest="backlog_cmd", required=True)
    add_p = backlog_sub.add_parser("add", help="append a new mission")
    add_p.add_argument("title", help="short label for this mission")
    add_p.add_argument(
        "--objective",
        required=True,
        help="full objective text shown to the engineer",
    )
    add_p.add_argument("--priority", type=int, default=100)
    add_p.add_argument("--max-cost-usd", type=float, default=1.0)
    add_p.add_argument(
        "--tag",
        action="append",
        default=[],
        dest="tags",
        help="tag (may repeat); used by memory retrieval",
    )

    list_p = backlog_sub.add_parser("list", help="show backlog items")
    list_p.add_argument(
        "--all",
        action="store_true",
        help="include done / failed / skipped items",
    )

    rm_p = backlog_sub.add_parser("remove", help="remove a backlog item by id")
    rm_p.add_argument("item_id")

    done_p = backlog_sub.add_parser(
        "done", help="manually mark a backlog item as done"
    )
    done_p.add_argument("item_id")

    skip_p = backlog_sub.add_parser(
        "skip", help="manually mark a backlog item as skipped"
    )
    skip_p.add_argument("item_id")

    # journal group
    journal_p = life_sub.add_parser("journal", help="inspect or extend the journal")
    journal_sub = journal_p.add_subparsers(dest="journal_cmd", required=True)
    tail_p = journal_sub.add_parser("tail", help="show the last N entries")
    tail_p.add_argument("-n", "--limit", type=int, default=10)
    note_p = journal_sub.add_parser("note", help="append a manual note")
    note_p.add_argument("text", help="note text")
    note_p.add_argument("--title", default="manual note")
    note_p.add_argument("--tag", action="append", default=[], dest="tags")

    # run
    run_p = life_sub.add_parser(
        "run", help="drive the supervisor: pull backlog items, run missions, repeat"
    )
    run_p.add_argument(
        "--backend",
        choices=("memory", "codex"),
        default=os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex"),
        help="codex (default): real LLM via codex CLI. "
             "memory: deterministic in-process stub (no API calls, no work done) "
             "— only useful for smoke tests / CI.",
    )
    run_p.add_argument(
        "--once",
        action="store_true",
        help="run one mission and exit",
    )
    run_p.add_argument("--max-missions", type=int, default=3)
    run_p.add_argument("--per-mission-cap-usd", type=float, default=1.0)
    run_p.add_argument("--daily-cap-usd", type=float, default=5.0)
    run_p.add_argument(
        "--engineer-model",
        default=os.environ.get("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini"),
    )
    run_p.add_argument(
        "--reviewer-model",
        default=os.environ.get("ARGUS_SKILL_REVIEWER_MODEL", "gpt-5.4"),
    )
    run_p.add_argument(
        "--scientist-model",
        default=os.environ.get("ARGUS_SKILL_SCIENTIST_MODEL", "gpt-5.4"),
    )
    run_p.add_argument(
        "--skills-dir",
        default=os.environ.get("ARGUS_SKILL_SKILLS_DIR", "skills"),
    )
    run_p.add_argument(
        "--workdir",
        default=os.environ.get("ARGUS_SKILL_WORKDIR"),
        help="cwd for the engineer (default: cwd)",
    )
    run_p.add_argument("--max-rounds", type=int, default=3)
    run_p.add_argument(
        "--quiet",
        action="store_true",
        help="suppress per-event chatter (still writes journal)",
    )

    # chat — interactive REPL (no file editing required)
    life_sub.add_parser(
        "chat",
        help="interactive REPL (slash commands + free text). "
             "Auto-inits the life dir on first use.",
    )


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------

def cmd_life(args: argparse.Namespace) -> int:
    """Dispatch a ``life`` subcommand."""
    root = Path(args.life_dir).expanduser() if args.life_dir else default_life_dir()
    mem = LifeMemory.open(root)

    sub = args.life_cmd
    if sub == "init":
        return _cmd_init(mem)
    if sub == "status":
        return _cmd_status(mem)
    if sub == "backlog":
        return _cmd_backlog(mem, args)
    if sub == "journal":
        return _cmd_journal(mem, args)
    if sub == "run":
        return _cmd_run(mem, args)
    if sub == "chat":
        return _cmd_chat(mem, args)
    print(f"unknown life subcommand: {sub}", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# init / status
# ---------------------------------------------------------------------------

def _cmd_init(mem: LifeMemory) -> int:
    state = mem.init()
    created = [k for k, v in state.items() if v]
    if created:
        print(f"created: {', '.join(created)}")
    else:
        print("(no changes — life directory already initialised)")
    print(f"life dir: {mem.root}")
    return 0


def _cmd_status(mem: LifeMemory) -> int:
    print(f"## argus-skill life — {mem.root}\n")

    identity = mem.identity.read().strip()
    print("### identity")
    if identity:
        # Show only the first ~10 lines so status stays compact.
        head = "\n".join(identity.splitlines()[:12])
        print(head)
    else:
        print("(no identity card — run `life init`)")
    print()

    backlog_all = mem.backlog.all()
    pending = [it for it in backlog_all if it.status == "pending"]
    print(f"### backlog ({len(pending)} pending / {len(backlog_all)} total)")
    if not backlog_all:
        print("(empty)")
    else:
        for it in pending[:5]:
            print(
                f"- [{it.priority}] {it.title}  "
                f"(${it.max_cost_usd:.2f}, id={it.id})"
            )
        rest = max(0, len(pending) - 5)
        if rest:
            print(f"  … and {rest} more pending")
        # Done / failed counts.
        by_status: dict[str, int] = {}
        for it in backlog_all:
            by_status[it.status] = by_status.get(it.status, 0) + 1
        if any(s != "pending" for s in by_status):
            tail = ", ".join(
                f"{s}={n}" for s, n in by_status.items() if s != "pending"
            )
            print(f"  history: {tail}")
    print()

    entries = mem.journal.tail(5)
    print(f"### journal (last {len(entries)} entries)")
    if not entries:
        print("(empty)")
    else:
        for e in entries:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.ts))
            cost = f"${e.cost_usd:.4f}" if e.cost_usd else ""
            print(f"- {ts} · [{e.kind}] {e.title} {cost}")
    return 0


# ---------------------------------------------------------------------------
# backlog
# ---------------------------------------------------------------------------

def _cmd_backlog(mem: LifeMemory, args: argparse.Namespace) -> int:
    cmd = args.backlog_cmd
    if cmd == "add":
        item = mem.backlog.add(
            BacklogItem.new(
                title=args.title,
                objective=args.objective,
                priority=args.priority,
                max_cost_usd=args.max_cost_usd,
                tags=args.tags,
            )
        )
        print(f"added {item.id}: {item.title} (priority={item.priority})")
        return 0
    if cmd == "list":
        items = mem.backlog.all() if args.all else [
            it for it in mem.backlog.all() if it.status == "pending"
        ]
        if not items:
            print("(none)")
            return 0
        items.sort(key=lambda it: (it.priority, it.ts))
        for it in items:
            print(
                f"{it.id}  [{it.status}]  p={it.priority:>3}  "
                f"${it.max_cost_usd:.2f}  {it.title}"
            )
        return 0
    if cmd == "remove":
        ok = mem.backlog.remove(args.item_id)
        print("removed" if ok else f"no such item: {args.item_id}")
        return 0 if ok else 1
    if cmd == "done":
        out = mem.backlog.mark_done(args.item_id)
        if out is None:
            print(f"no such item: {args.item_id}")
            return 1
        print(f"marked done: {out.title}")
        return 0
    if cmd == "skip":
        out = mem.backlog.update(args.item_id, status="skipped")
        if out is None:
            print(f"no such item: {args.item_id}")
            return 1
        print(f"skipped: {out.title}")
        return 0
    print(f"unknown backlog subcommand: {cmd}", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------

def _cmd_journal(mem: LifeMemory, args: argparse.Namespace) -> int:
    cmd = args.journal_cmd
    if cmd == "tail":
        entries = mem.journal.tail(args.limit)
        if not entries:
            print("(empty)")
            return 0
        for e in entries:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.ts))
            cost = f"  cost=${e.cost_usd:.4f}" if e.cost_usd else ""
            tags = f"  tags={','.join(e.tags)}" if e.tags else ""
            print(f"[{ts}] [{e.kind}] {e.title}{cost}{tags}")
            if e.summary:
                print(f"    {e.summary}")
        return 0
    if cmd == "note":
        entry = JournalEntry.new(
            kind="user_note",
            title=args.title,
            summary=args.text,
            tags=args.tags,
        )
        mem.journal.append(entry)
        print(f"note appended (id={entry.id})")
        return 0
    print(f"unknown journal subcommand: {cmd}", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# run — supervisor driver
# ---------------------------------------------------------------------------

def _cmd_run(mem: LifeMemory, args: argparse.Namespace) -> int:
    backend_name = args.backend
    print(
        f"life: backend={backend_name} "
        f"max_missions={args.max_missions} "
        f"per_mission_cap=${args.per_mission_cap_usd:.2f} "
        f"daily_cap=${args.daily_cap_usd:.2f}",
        file=sys.stderr,
    )

    runner = _build_runner(args)
    summary = _run_supervisor(
        mem=mem,
        runner=runner,
        engineer_model=args.engineer_model,
        reviewer_model=args.reviewer_model,
        once=args.once,
        max_missions=args.max_missions,
        per_mission_cap_usd=args.per_mission_cap_usd,
        daily_cap_usd=args.daily_cap_usd,
        quiet=args.quiet,
    )
    print(json.dumps(summary, indent=2, default=str))
    # Exit 0 even on partial — the supervisor stopping due to budget /
    # cap is expected behaviour, not a CLI error.
    return 0


def _run_supervisor(
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
) -> dict[str, Any]:
    """Run LifeSupervisor with proper signal-handler save/restore.

    Used by both `life run` and `life chat /run`. Restoring previous
    handlers on exit means chat can keep its REPL Ctrl-C semantics
    after a /run finishes.
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
        sink = _StderrSink(quiet=quiet)
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


# ---------------------------------------------------------------------------
# chat — interactive REPL
# ---------------------------------------------------------------------------

_CHAT_HELP = """\
argus-skill life chat — interactive lifetime-agent REPL

Slash commands:
  /help                     show this help
  /status                   summary of identity, backlog, recent journal
  /identity                 print current identity card
  /identity edit            multi-line input until a single '.' on its own
  /identity set <text>      one-line replacement of the identity card

  /backlog                  list pending items
  /backlog all              list every item (incl. done/skipped/failed)
  /add <text>               enqueue a mission WITHOUT running it
  /done <id>                mark backlog item as done
  /skip <id>                mark backlog item as skipped
  /rm <id>                  remove backlog item

  /journal [N]              tail last N journal entries (default 10)
  /note <text>              append a manual journal note

  /run [opts]               drain the entire backlog (foreground; Ctrl-C stops)
                            opts: --once  --backend memory|codex
                                  --max-missions N
                                  --per-mission-cap-usd X
                                  --daily-cap-usd X
                                  --quiet
  /backend [codex|memory]   show / set the default backend used by free text
                            and /run when no --backend is given

  /quit  /exit              leave the REPL (Ctrl-D also works)

Free text (no leading '/') is treated as a one-shot command: it gets
appended to the backlog AND runs immediately on the current default
backend (codex by default — real tokens). Use /add if you only want to
enqueue without running.
"""


def _cmd_chat(mem: LifeMemory, args: argparse.Namespace) -> int:
    """Interactive REPL — replaces file editing with slash commands."""
    import shlex
    from ._input_helpers import read_pasted_message

    # Auto-init on first use so users don't need a separate `life init`.
    state = mem.init()
    created = [k for k, v in state.items() if v]
    if created:
        print(f"initialized life memory at {mem.root}")
        print(f"  created: {', '.join(created)}")
    else:
        print(f"life memory: {mem.root}")

    # Default backend used by free-text and bare /run. Env override; codex
    # is the real one. memory is a deterministic stub kept around for
    # CI / no-API-key smoke tests.
    chat_state = {
        "backend": os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex"),
    }
    print(f"backend: {chat_state['backend']}  (change with /backend memory|codex)")
    print("Type /help for commands.  Free text runs immediately on the backend.")

    while True:
        try:
            raw = read_pasted_message("life> ")
        except KeyboardInterrupt:
            print()
            continue
        if raw is None:  # EOF
            print()
            return 0
        line = raw.strip()
        if not line:
            continue

        if not line.startswith("/"):
            _chat_run_freeform(mem, args, raw, chat_state)
            continue

        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            print(f"parse error: {exc}")
            continue
        cmd = tokens[0].lower()
        rest = tokens[1:]
        rest_text = line[len(tokens[0]):].lstrip()

        if cmd in ("/quit", "/exit"):
            return 0
        if cmd == "/help":
            print(_CHAT_HELP)
            continue
        if cmd == "/status":
            _cmd_status(mem)
            continue
        if cmd == "/identity":
            _chat_identity(mem, rest, rest_text)
            continue
        if cmd == "/backlog":
            include_all = bool(rest) and rest[0].lower() == "all"
            _chat_backlog_list(mem, include_all=include_all)
            continue
        if cmd == "/add":
            if not rest_text:
                print("usage: /add <objective text>")
                continue
            _chat_add_only(mem, rest_text)
            continue
        if cmd in ("/done", "/skip", "/rm"):
            if not rest:
                print(f"usage: {cmd} <item_id>")
                continue
            _chat_status_change(mem, cmd, rest[0])
            continue
        if cmd == "/journal":
            n = 10
            if rest:
                try:
                    n = int(rest[0])
                except ValueError:
                    print(f"usage: /journal [N]  (got: {rest[0]!r})")
                    continue
            _chat_journal_tail(mem, n)
            continue
        if cmd == "/note":
            if not rest_text:
                print("usage: /note <text>")
                continue
            entry = JournalEntry.new(
                kind="user_note",
                title="manual note",
                summary=rest_text,
                tags=[],
            )
            mem.journal.append(entry)
            print(f"note appended (id={entry.id})")
            continue
        if cmd == "/backend":
            _chat_backend(rest, chat_state)
            continue
        if cmd == "/run":
            _chat_run(mem, args, rest, chat_state)
            continue
        print(f"unknown command: {cmd}  (try /help)")


def _chat_add_only(mem: LifeMemory, text: str) -> "BacklogItem":
    """Append a backlog item from a free-form objective. No execution."""
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


def _chat_run_freeform(
    mem: LifeMemory,
    base_args: argparse.Namespace,
    text: str,
    chat_state: dict[str, Any],
) -> None:
    """Free-text input: enqueue + run immediately on the current backend."""
    item = _chat_add_only(mem, text)
    print(f"running on backend={chat_state['backend']} (Ctrl-C to stop)...")
    _chat_invoke_supervisor(
        mem=mem,
        backend=chat_state["backend"],
        once=True,
        max_missions=1,
        per_mission_cap_usd=1.0,
        daily_cap_usd=5.0,
        quiet=False,
    )


def _chat_backend(tokens: list[str], chat_state: dict[str, Any]) -> None:
    if not tokens:
        print(f"backend: {chat_state['backend']}")
        return
    new = tokens[0].lower()
    if new not in ("codex", "memory"):
        print(f"unknown backend: {new}  (codex|memory)")
        return
    chat_state["backend"] = new
    print(f"backend set to {new}")


def _chat_identity(mem: LifeMemory, tokens: list[str], rest_text: str) -> None:
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


def _chat_backlog_list(mem: LifeMemory, *, include_all: bool) -> None:
    items = mem.backlog.all()
    if not include_all:
        items = [it for it in items if it.status == "pending"]
    if not items:
        print("(none)")
        return
    items.sort(key=lambda it: (it.priority, it.ts))
    for it in items:
        print(
            f"{it.id}  [{it.status}]  p={it.priority:>3}  "
            f"${it.max_cost_usd:.2f}  {it.title}"
        )


def _chat_status_change(mem: LifeMemory, cmd: str, item_id: str) -> None:
    if cmd == "/done":
        out = mem.backlog.mark_done(item_id)
        msg = f"marked done: {out.title}" if out else f"no such item: {item_id}"
    elif cmd == "/skip":
        out = mem.backlog.update(item_id, status="skipped")
        msg = f"skipped: {out.title}" if out else f"no such item: {item_id}"
    elif cmd == "/rm":
        ok = mem.backlog.remove(item_id)
        msg = "removed" if ok else f"no such item: {item_id}"
    else:
        msg = f"unknown: {cmd}"
    print(msg)


def _chat_journal_tail(mem: LifeMemory, n: int) -> None:
    entries = mem.journal.tail(n)
    if not entries:
        print("(empty)")
        return
    for e in entries:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.ts))
        cost = f"  cost=${e.cost_usd:.4f}" if e.cost_usd else ""
        tags = f"  tags={','.join(e.tags)}" if e.tags else ""
        print(f"[{ts}] [{e.kind}] {e.title}{cost}{tags}")
        if e.summary:
            print(f"    {e.summary}")


def _chat_run(
    mem: LifeMemory,
    base_args: argparse.Namespace,
    opts: list[str],
    chat_state: dict[str, Any],
) -> None:
    """Parse /run options and run the supervisor in foreground."""
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

    summary = _chat_invoke_supervisor(
        mem=mem,
        backend=run_args.backend,
        once=run_args.once,
        max_missions=run_args.max_missions,
        per_mission_cap_usd=run_args.per_mission_cap_usd,
        daily_cap_usd=run_args.daily_cap_usd,
        quiet=run_args.quiet,
    )
    print("\n--- /run summary ---")
    print(json.dumps(summary, indent=2, default=str))


def _chat_invoke_supervisor(
    *,
    mem: LifeMemory,
    backend: str,
    once: bool,
    max_missions: int,
    per_mission_cap_usd: float,
    daily_cap_usd: float,
    quiet: bool,
) -> dict[str, Any]:
    """Build a runner Namespace, drive the supervisor, return its summary."""
    ns = argparse.Namespace()
    ns.backend = backend
    ns.engineer_model = os.environ.get("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini")
    ns.reviewer_model = os.environ.get("ARGUS_SKILL_REVIEWER_MODEL", "gpt-5.4")
    ns.scientist_model = os.environ.get("ARGUS_SKILL_SCIENTIST_MODEL", "gpt-5.4")
    ns.skills_dir = os.environ.get("ARGUS_SKILL_SKILLS_DIR", "skills")
    ns.workdir = os.environ.get("ARGUS_SKILL_WORKDIR")
    ns.max_rounds = 3

    runner = _build_runner(ns)
    return _run_supervisor(
        mem=mem,
        runner=runner,
        engineer_model=ns.engineer_model,
        reviewer_model=ns.reviewer_model,
        once=once,
        max_missions=max_missions,
        per_mission_cap_usd=per_mission_cap_usd,
        daily_cap_usd=daily_cap_usd,
        quiet=quiet,
    )


# ---------------------------------------------------------------------------
# Runner adapters
# ---------------------------------------------------------------------------

class _StderrSink:
    """Forward events to stderr in human-readable form."""

    def __init__(self, *, quiet: bool) -> None:
        self.quiet = quiet

    def handle_event(self, event: dict[str, Any]) -> None:
        if self.quiet:
            return
        kind = event.get("type", "?")
        text = event.get("text") or event.get("title") or ""
        sys.stderr.write(f"[{kind}] {text}\n")
        sys.stderr.flush()


def _build_runner(args: argparse.Namespace):
    """Return a ``_MissionRunner``-shaped adapter."""
    if args.backend == "memory":
        return _MemoryRunner()
    if args.backend == "codex":
        return _CodexSkillLoopRunner(args)
    raise SystemExit(f"unknown backend: {args.backend}")


@dataclass
class _Outcome:
    """Duck-typed outcome the supervisor reads via ``getattr``.

    Mirrors ``MissionOutcome``'s public attributes; values default to
    ``False`` / ``""`` so the supervisor's getattr-with-default path is
    happy when an adapter doesn't have a richer notion of e.g. follow-up.
    """
    success: bool
    status: str
    stop_reason: str = ""
    rounds: int = 1
    matched_skill_name: str | None = None
    skill_distilled: bool = False
    had_follow_up: bool = False


class _MemoryRunner:
    """Deterministic in-process runner for shape-tests and CI demos.

    Pretends to do work: emits one ``round.main.completed`` event with
    a tiny token count (so cost is non-zero but trivial), pretends the
    mission succeeded, and returns. Useful for verifying the life loop
    end-to-end without burning real tokens.
    """

    def execute(
        self,
        *,
        objective: str,
        sink: EventSink,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
    ) -> _Outcome:
        # A modest token charge so daily budgets are testable.
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

    This is the simpler pipeline (no full mission engine planner /
    follow-up phase). Wired here so ``life run --backend codex`` works
    out of the box without requiring the full ``MissionExecutor``
    construction (which needs ArgusBot's CodexRunner / Reviewer /
    Planner pre-built — that wiring is duplicated in the queue daemon
    and SWE-Bench-Pro adapter and is slated to move into a shared
    factory in Phase 3.B).

    Memory injection: the prelude is prepended to the engineer's
    objective with a clear "memory context" header. Yes — the matcher
    sees it too. The header is generic enough that this rarely shifts
    skill matches in practice; the cleaner channelisation is Phase 3.B.
    """

    def __init__(self, args: argparse.Namespace) -> None:
        # Lazy import: ArgusBot may be missing in lightweight envs.
        from .cli import _load_backend
        from ..loop import SkillLoop, SkillLoopConfig

        self._SkillLoop = SkillLoop
        self._SkillLoopConfig = SkillLoopConfig
        self._backend = _load_backend()
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


__all__ = ["add_life_subcommand", "cmd_life"]
