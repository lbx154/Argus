"""Headless teammate entrypoint.

Run as::

    python -m argus_skill.team.teammate_entry --root <team_root> --member-id <id> \
        [--task-id <id>] [--cwd <dir>]

Finds the task this member owns on the shared board and runs ONE headless Argus
engineer mission on that task's objective — **in-process**, reusing the exact
per-mission call the daemon's supervisor makes (``_CodexSkillLoopRunner.execute``)
— heartbeating the board while it runs, then marking the task done/failed and
writing a result shard when the mission returns.

Why in-process (not ``python -m argus_skill ...``): the CLI only offers the
interactive cockpit (drops to the REPL, dies on EOF, no-op ``rc=0``) or a full
``--daemon-fg`` daemon (acquires the per-project daemon lock + runs its own
planner → would recurse into nested teams). Calling the runner directly gives a
single headless engineer mission with **no cockpit, no daemon lock, no planner,
no recursion**, and needs no project memory. ``life_dir`` only scopes where this
teammate's ``events.jsonl`` is written, so each teammate is isolated.

This is what ``tools/team.py spawn`` launches, so teams work out of the box —
the lead never hand-rolls a launcher.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import task_board


def _build_runner_ns(cwd: str, *, max_rounds: int, paper_mission: bool) -> argparse.Namespace:
    """Replicate the daemon's runner namespace (life_worker._runner_namespace)."""
    from argus_skill.core import paths as core_paths
    from argus_skill.tools.capability_vault import resolve_route_model

    ns = argparse.Namespace()
    ns.backend = os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex")
    ns.engineer_model = os.environ.get("ARGUS_SKILL_ENGINEER_MODEL") or resolve_route_model("engineer")
    ns.reviewer_model = os.environ.get("ARGUS_SKILL_REVIEWER_MODEL") or resolve_route_model("reviewer")
    ns.scientist_model = os.environ.get("ARGUS_SKILL_SCIENTIST_MODEL") or resolve_route_model("scientist")
    ns.engineer_reasoning_effort = os.environ.get("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "high")
    ns.reviewer_reasoning_effort = os.environ.get("ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "high")
    ns.scientist_reasoning_effort = os.environ.get("ARGUS_SKILL_SCIENTIST_REASONING_EFFORT", "high")
    ns.skills_dir = os.environ.get("ARGUS_SKILL_SKILLS_DIR", str(core_paths.skills_global_root()))
    ns.workdir = str(cwd)
    ns.max_rounds = int(os.environ.get("ARGUS_SKILL_MAX_ROUNDS", str(max_rounds)))
    ns.plan_mode = os.environ.get("ARGUS_SKILL_PLAN_MODE", "auto")
    ns.plan_model = os.environ.get("ARGUS_SKILL_PLAN_MODEL")
    ns.check = []
    ns.check_commands = []
    ns.color = None
    ns.verbose = False
    ns.quiet = True
    # Teammate optimizes one kernel — not a paper — so keep the EMNLP paper gates off.
    ns.paper_mission = paper_mission
    return ns


def run_one_engineer_mission(objective: str, *, cwd: str, life_dir: Path,
                             paper_mission: bool = False, max_rounds: int = 200) -> bool:
    """Run ONE headless engineer mission in-process on ``objective`` in ``cwd``.

    Reuses ``_CodexSkillLoopRunner.execute`` — the exact per-mission call the
    daemon's supervisor makes. No cockpit, no daemon lock, no planner, no
    recursion. Events go to the isolated ``life_dir``. Returns True on success.
    """
    try:
        from argus_skill.apps._life_repl import LifeStderrSink, _CodexSkillLoopRunner
        from argus_skill.life.event_log import JsonlEventSink
    except Exception as exc:  # noqa: BLE001 — import/wiring problem
        sys.stderr.write(f"teammate_entry: cannot import runner: {exc}\n")
        return False
    life_dir = Path(life_dir)
    life_dir.mkdir(parents=True, exist_ok=True)
    ns = _build_runner_ns(cwd, max_rounds=max_rounds, paper_mission=paper_mission)
    try:
        runner = _CodexSkillLoopRunner(ns)
        sink = JsonlEventSink(LifeStderrSink(quiet=False), life_dir=life_dir)
        outcome = runner.execute(objective=objective, sink=sink)
    except SystemExit as exc:  # codex extra missing, etc.
        sys.stderr.write(f"teammate_entry: runner unavailable: {exc}\n")
        return False
    except Exception as exc:  # noqa: BLE001 — never let a mission crash kill bookkeeping
        sys.stderr.write(f"teammate_entry: mission error: {exc!r}\n")
        return False
    return bool(getattr(outcome, "success", False))


def _owned_task(root: Path, member_id: str, task_id: str | None) -> dict | None:
    tasks = task_board.snapshot(root)
    if task_id:
        for x in tasks:
            if x["task_id"] == task_id:
                return x
    for x in tasks:
        if x.get("owner") == member_id:
            return x
    return None


def _heartbeat_loop(root: Path, task_id: str, stop: threading.Event) -> None:
    while not stop.wait(30.0):
        task_board.heartbeat(root, task_id, now=time.time())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="argus_skill.team.teammate_entry")
    p.add_argument("--root", required=True)
    p.add_argument("--member-id", required=True)
    p.add_argument("--task-id", default="")
    p.add_argument("--cwd", default="")
    p.add_argument("--mission-cmd", default="",
                   help="test override: run this command (+objective) instead of the in-process mission")
    args = p.parse_args(argv)

    root = Path(args.root)
    task = _owned_task(root, args.member_id, args.task_id or None)
    if task is None:
        sys.stderr.write(f"teammate_entry: no task for {args.member_id}\n")
        return 2
    task_id = task["task_id"]
    objective = task.get("objective", "")
    cwd = args.cwd or os.getcwd()
    member_safe = args.member_id.replace(":", "_")

    (root / "shards").mkdir(parents=True, exist_ok=True)
    shard = root / "shards" / (member_safe + ".jsonl")

    task_board.heartbeat(root, task_id, now=time.time())
    stop = threading.Event()
    threading.Thread(target=_heartbeat_loop, args=(root, task_id, stop), daemon=True).start()

    if args.mission_cmd:
        # test/escape-hatch path: run an arbitrary stub command instead of the mission
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / (member_safe + ".log"), "ab") as log, open(os.devnull, "rb") as devnull:
            proc = subprocess.Popen(args.mission_cmd.split() + [objective], cwd=cwd,
                                    stdin=devnull, stdout=log, stderr=log, start_new_session=True)
            success = proc.wait() == 0
    else:
        success = run_one_engineer_mission(
            objective, cwd=cwd, life_dir=root / "life" / member_safe)

    stop.set()
    shard.write_text(
        json.dumps({"member_id": args.member_id, "task_id": task_id, "success": success}) + "\n",
        encoding="utf-8")
    if success:
        task_board.complete(root, task_id, shard=str(shard))
    else:
        task_board.fail(root, task_id, reason="teammate mission did not succeed")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
