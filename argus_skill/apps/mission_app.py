"""``argus-skill mission`` subcommands: start / stop / status.

A *mission* is a sustained, multi-round objective that ArgusBot's
``LoopEngine`` drives autonomously — operator sets the objective +
acceptance criteria once, then walks away. The daemon (when started
with ``--mission-file``) loads the mission and runs ``LoopEngine.run()``
in its worker thread.

Subcommands wired here:

  * ``mission start "<objective>"`` — write a ``mission.json`` to
    ``state-dir/missions/<mission_id>/`` and point
    ``state-dir/missions/active.json`` at it. Does NOT start the daemon
    — operator either restarts the daemon with ``--mission-file`` or
    sends a ``/mode auto`` command to a running mission daemon to
    pick up the new mission.

  * ``mission status`` — read the active mission's metadata + the
    daemon's status.json and print a one-screen summary.

  * ``mission stop`` — send a ``stop`` command via the JSONL bus,
    terminating the active mission on the daemon. Equivalent to
    ``daemon-stop`` but spelled in mission-vocabulary.

Mission file layout::

    state-dir/
        missions/
            active.json        # {"mission_id": "mission_..."}
            mission_20260504T163000Z/
                mission.json   # objective, criteria, plan_mode, ...
                loop_state/    # LoopEngine's LoopStateStore artifacts
                    operator_messages.txt
                    plan_overview.md
                    review_summaries.md
                    round_summaries.md
                    ...

This split keeps mission state isolated from the daemon's persistent
``status.json`` / ``inbox.jsonl`` / ``outbox.jsonl`` (those are
process-level, not mission-level).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ..daemon.bus import BusCommand, JsonlCommandBus

# ---------------------------------------------------------------------------
# Mission ID + filesystem layout
# ---------------------------------------------------------------------------

MISSION_ACTIVE_FILE = "active.json"


def _utc_mission_id() -> str:
    """ID like 'mission_20260504T163055Z' — sortable, human-readable."""
    return "mission_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _mission_root(state_dir: str | Path) -> Path:
    base = Path(state_dir) / "missions"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _mission_dir(state_dir: str | Path, mission_id: str) -> Path:
    d = _mission_root(state_dir) / mission_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "loop_state").mkdir(parents=True, exist_ok=True)
    return d


def _active_pointer_path(state_dir: str | Path) -> Path:
    return _mission_root(state_dir) / MISSION_ACTIVE_FILE


def _read_active_mission(state_dir: str | Path) -> dict | None:
    p = _active_pointer_path(state_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_active_mission(state_dir: str | Path, mission_id: str) -> None:
    p = _active_pointer_path(state_dir)
    payload = {"mission_id": mission_id, "set_at": time.time()}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# argparse plumbing
# ---------------------------------------------------------------------------

VALID_PLAN_MODES = ("off", "auto", "record")


def add_mission_subcommands(sub: argparse._SubParsersAction) -> None:
    start_p = sub.add_parser(
        "mission",
        help="manage a sustained 7×24 mission (start/stop/status)",
    )
    inner = start_p.add_subparsers(dest="mission_cmd", required=True)

    s = inner.add_parser("start", help="start a new mission (write mission.json)")
    s.add_argument("objective", help="the mission objective (free-form prompt)")
    s.add_argument(
        "--state-dir",
        default=".argus-skill",
        help="argus-skill state dir (default: .argus-skill)",
    )
    s.add_argument(
        "--workdir",
        default=None,
        help="working directory the engineer runs in (default: cwd at daemon start)",
    )
    s.add_argument(
        "--check",
        action="append",
        default=[],
        help="acceptance check command (may repeat). LoopEngine runs these between rounds.",
    )
    s.add_argument("--max-rounds", type=int, default=50)
    s.add_argument(
        "--plan-mode",
        choices=VALID_PLAN_MODES,
        default="auto",
        help=(
            "auto: planner proposes follow-ups when reviewer says done (true 7×24); "
            "off: stop after first done; record: planner records but doesn't auto-execute."
        ),
    )
    s.add_argument(
        "--main-model",
        default=os.environ.get("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini"),
        help="model for the per-round main agent",
    )
    s.add_argument(
        "--reviewer-model",
        default=os.environ.get("ARGUS_SKILL_REVIEWER_MODEL", "gpt-5.4-mini"),
        help="model for LoopEngine's mission-level reviewer",
    )
    s.add_argument(
        "--plan-model",
        default=os.environ.get("ARGUS_SKILL_PLAN_MODEL", "gpt-5.4"),
        help="model for the planner agent (only used in plan-mode auto/record)",
    )
    s.add_argument(
        "--main-reasoning-effort",
        default=os.environ.get("ARGUS_SKILL_ENGINEER_REASONING", "medium"),
    )
    s.add_argument(
        "--reviewer-reasoning-effort",
        default=os.environ.get("ARGUS_SKILL_REVIEWER_REASONING", "medium"),
    )
    s.add_argument(
        "--plan-reasoning-effort",
        default=os.environ.get("ARGUS_SKILL_PLAN_REASONING", "high"),
    )

    st = inner.add_parser("status", help="show the active mission and daemon status")
    st.add_argument("--state-dir", default=".argus-skill")

    stop = inner.add_parser(
        "stop",
        help="stop the active mission on the running daemon (sends a stop command)",
    )
    stop.add_argument("--state-dir", default=".argus-skill")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_mission(args: argparse.Namespace) -> int:
    if args.mission_cmd == "start":
        return cmd_mission_start(args)
    if args.mission_cmd == "status":
        return cmd_mission_status(args)
    if args.mission_cmd == "stop":
        return cmd_mission_stop(args)
    print("unknown mission subcommand", file=sys.stderr)
    return 2


def cmd_mission_start(args: argparse.Namespace) -> int:
    if args.plan_mode not in VALID_PLAN_MODES:
        print(
            f"--plan-mode must be one of {VALID_PLAN_MODES} (got {args.plan_mode!r})",
            file=sys.stderr,
        )
        return 2
    if not args.objective.strip():
        print("objective must be non-empty", file=sys.stderr)
        return 2

    mission_id = _utc_mission_id()
    mdir = _mission_dir(args.state_dir, mission_id)
    workdir = args.workdir or os.getcwd()

    payload = {
        "mission_id": mission_id,
        "objective": args.objective.strip(),
        "workdir": workdir,
        "check_commands": list(args.check or []),
        "max_rounds": int(args.max_rounds),
        "plan_mode": args.plan_mode,
        "main_model": args.main_model,
        "reviewer_model": args.reviewer_model,
        "plan_model": args.plan_model,
        "main_reasoning_effort": args.main_reasoning_effort,
        "reviewer_reasoning_effort": args.reviewer_reasoning_effort,
        "plan_reasoning_effort": args.plan_reasoning_effort,
        "started_at": time.time(),
        "started_at_iso": datetime.now(timezone.utc).isoformat(),
    }
    mission_file = mdir / "mission.json"
    mission_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    _write_active_mission(args.state_dir, mission_id)

    print(f"mission_id  : {mission_id}")
    print(f"mission.json: {mission_file}")
    print(f"active.json : {_active_pointer_path(args.state_dir)}")
    if not getattr(args, "quiet", False):
        print()
        print("Next:")
        print(f"  argus-skill daemon --mission-file {mission_file} --state-dir {args.state_dir}")
        print("  (or restart your systemd unit with the same flag)")
    return 0


def cmd_mission_status(args: argparse.Namespace) -> int:
    active = _read_active_mission(args.state_dir)
    if not active:
        print("no active mission", file=sys.stderr)
        return 1
    mission_id = active.get("mission_id", "?")
    mfile = _mission_root(args.state_dir) / mission_id / "mission.json"
    if not mfile.exists():
        print(f"active mission {mission_id} but {mfile} is missing", file=sys.stderr)
        return 1
    payload = json.loads(mfile.read_text())

    # Best-effort: also show daemon status.json side-by-side.
    daemon_status = None
    status_path = Path(args.state_dir) / "status.json"
    if status_path.exists():
        try:
            daemon_status = json.loads(status_path.read_text())
        except (OSError, json.JSONDecodeError):
            daemon_status = None

    output = {
        "active_mission": payload,
        "daemon_status": daemon_status,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def cmd_mission_stop(args: argparse.Namespace) -> int:
    inbox = Path(args.state_dir) / "inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    bus = JsonlCommandBus(str(inbox))
    bus.publish(BusCommand(kind="stop", text="", source="mission-stop", ts=time.time()))
    print(f"sent stop -> {inbox}")
    return 0


__all__ = [
    "MISSION_ACTIVE_FILE",
    "VALID_PLAN_MODES",
    "add_mission_subcommands",
    "cmd_mission",
    "cmd_mission_start",
    "cmd_mission_status",
    "cmd_mission_stop",
]
