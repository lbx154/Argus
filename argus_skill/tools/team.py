"""Agent-facing control CLI for Argus Agent Teams.

The daemon-resident Curator exclusively owns teammate process lifetime.  This
module only exposes the lead's durable control-plane operations: form/refresh a
backlog, inspect it, change pool intent, and dissolve the campaign.  It does not
provide a second manual spawn/reap path.

Verbs: form / status / dissolve / pool-set.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from ..team import pool, registry, roster, task_board


def _load_tasks(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    if not out:
        raise ValueError(f"team backlog is empty: {path}")
    return out


def cmd_form(a: argparse.Namespace) -> int:
    project_root = os.environ.get("ARGUS_SKILL_PROJECT_ROOT", "").strip()
    if not project_root:
        print(
            "team form requires a running project environment "
            "(ARGUS_SKILL_PROJECT_ROOT is unset)",
            file=sys.stderr,
        )
        return 2

    root = Path(a.root)
    tasks = _load_tasks(Path(a.tasks))
    roster.create(
        root,
        team_id=a.team_id,
        mission=a.mission,
        lead=a.lead,
        now=time.time(),
    )
    task_board.form(root, tasks)
    # Publish a new campaign paused, so the lead's explicit pool-set cannot race
    # the Curator's default-width refill. Re-forming preserves existing intent.
    if not (root / "pool.json").exists():
        pool.update(root, width=0, state="running")
    # The registry marker is the sole handoff to the resident Curator.
    registry.write_marker(
        Path(project_root),
        team_id=a.team_id,
        team_root=root,
        cwd=(a.cwd or os.getcwd()),
        now=time.time(),
    )
    return 0


def cmd_status(a: argparse.Namespace) -> int:
    root = Path(a.root)
    print(
        json.dumps(
            {
                "roster": roster.load(root),
                "members": roster.members(root),
                "tasks": task_board.snapshot(root),
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_dissolve(a: argparse.Namespace) -> int:
    root = Path(a.root)
    roster.set_state(root, "dissolved")
    # The Curator stops refilling and removes the marker once owned children
    # have exited.  It remains the only process reaper.
    pool.update(root, state="dissolved")
    return 0


def cmd_pool_set(a: argparse.Namespace) -> int:
    doc = pool.update(
        Path(a.root),
        width=a.width if a.width is not None else None,
        state=a.state or None,
    )
    print(json.dumps(doc, ensure_ascii=False))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="argus_skill.tools.team")
    sub = parser.add_subparsers(dest="cmd", required=True)

    form = sub.add_parser(
        "form",
        help="write/refresh the task backlog and register it with the Curator",
    )
    form.add_argument("--root", required=True)
    form.add_argument("--team-id", required=True)
    form.add_argument("--mission", default="")
    form.add_argument("--lead", default="lead")
    form.add_argument("--tasks", required=True)
    form.add_argument(
        "--cwd",
        default="",
        help="default working directory for tasks without their own cwd",
    )
    form.set_defaults(fn=cmd_form)

    status = sub.add_parser("status", help="show roster and task-board state")
    status.add_argument("--root", required=True)
    status.set_defaults(fn=cmd_status)

    dissolve = sub.add_parser(
        "dissolve",
        help="stop refilling and dissolve the campaign after children exit",
    )
    dissolve.add_argument("--root", required=True)
    dissolve.set_defaults(fn=cmd_dissolve)

    pool_set = sub.add_parser(
        "pool-set",
        help="set pool width/state intent for the resident Curator",
    )
    pool_set.add_argument("--root", required=True)
    pool_set.add_argument("--width", type=int, default=None)
    pool_set.add_argument("--state", default="", choices=["", "running", "draining"])
    pool_set.set_defaults(fn=cmd_pool_set)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
