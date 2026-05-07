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
import sys
import time
from pathlib import Path

from ..life import (
    BacklogItem,
    JournalEntry,
    LifeMemory,
)
from ..life.memory import default_life_dir


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

    # chat subcommand removed — interactive REPL is now `argus-skill chat --life`.
    # The non-interactive subcommands above remain for scripting.


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
    from ._life_repl import build_life_runner, run_life_supervisor

    backend_name = args.backend
    print(
        f"life: backend={backend_name} "
        f"max_missions={args.max_missions} "
        f"per_mission_cap=${args.per_mission_cap_usd:.2f} "
        f"daily_cap=${args.daily_cap_usd:.2f}",
        file=sys.stderr,
    )

    runner = build_life_runner(args)
    summary = run_life_supervisor(
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


__all__ = ["add_life_subcommand", "cmd_life"]
