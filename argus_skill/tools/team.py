"""Agent-facing CLI for Argus Agent Teams (the lead and teammates call this).

Thin wrapper over argus_skill.team.{task_board,mailbox,roster,worktree};
mirrors tools/subagent.py's CLI shape. ``spawn`` launches a teammate as a
detached bounded Argus mission inside a private worktree; tests override
the launched command with ``--exec-cmd``.

Verbs: form / spawn / status / wait / send / drain / claim / reassign / dissolve.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from ..team import mailbox, pool, roster, task_board, worktree


def _load_tasks(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def cmd_form(a: argparse.Namespace) -> int:
    task_board.form(Path(a.root), _load_tasks(Path(a.tasks)))
    if a.team_id:
        roster.create(Path(a.root), team_id=a.team_id, mission=a.mission,
                      lead=a.lead, now=time.time())
    return 0


def _spawn_teammate(root: Path, *, member_id: str, task_id: str, cwd: Path,
                    exec_cmd: str = "") -> int:
    """Launch ONE detached headless teammate on ``task_id``, record it on the
    roster, return its pid. Claiming is the caller's job (cmd_spawn uses
    claim_specific; the coordinator uses claim_top)."""
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (member_id.replace(":", "_") + ".spawn.log")
    if exec_cmd:
        argv = shlex.split(exec_cmd)
    else:
        # Built-in headless teammate entry — finds its task, runs a bounded mission
        # (NOT the interactive cockpit), heartbeats the board, records its shard.
        argv = [sys.executable, "-m", "argus_skill.team.teammate_entry",
                "--root", str(root), "--member-id", member_id,
                "--task-id", task_id, "--cwd", str(cwd)]
    with open(log_path, "ab") as log, open(os.devnull, "rb") as devnull:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=devnull, stdout=log,
                                stderr=log, start_new_session=True)
    roster.add_member(root, {
        "id": member_id, "pid": proc.pid, "worktree": str(cwd),
        "task_id": task_id, "status": "running", "heartbeat_ts": time.time(),
    })
    return proc.pid


def cmd_spawn(a: argparse.Namespace) -> int:
    root = Path(a.root)
    # teammate runs where the lead points (--cwd, default the workspace cwd). A
    # worktree is opt-in via --worktree for projects whose state is self-contained;
    # SOL-style projects need the live workspace (.venv/experiments/), so default off.
    cwd = Path(a.cwd) if a.cwd else Path.cwd()
    if a.worktree and a.repo:
        try:
            cwd = worktree.create(Path(a.repo), team_id=a.team_id, member_id=a.member_id)
        except Exception as exc:  # worktree optional
            print(f"team: worktree skipped: {exc}", file=sys.stderr)
    # Claim the SPECIFIC assigned task (not next-pending) so parallel spawns never
    # cross member IDs.
    claimed = task_board.claim_specific(root, a.task_id, a.member_id, now=time.time())
    task_id = claimed["task_id"] if claimed else a.task_id
    pid = _spawn_teammate(root, member_id=a.member_id, task_id=task_id,
                          cwd=cwd, exec_cmd=a.exec_cmd)
    print(json.dumps({"member_id": a.member_id, "pid": pid,
                      "task_id": task_id, "claimed": bool(claimed)}))
    return 0


def refill_once(root: Path, *, width: int, cwd: Path, member_prefix: str = "w",
                ttl: float, now: float, exec_cmd: str = "", spawn_fn=None) -> dict:
    """Top the in-flight teammate count back up to ``width`` from the backlog.

    Reassign stale (dead) teammates' tasks first, then claim the highest-priority
    pending tasks and spawn a fresh teammate on each until the pool is full or the
    backlog is empty. Idempotent: a second call with the pool already full spawns
    nothing. ``spawn_fn`` is injectable for tests.
    """
    spawn_fn = spawn_fn or _spawn_teammate
    reassigned = task_board.reassign_stale(root, ttl=ttl, now=now)
    in_flight = task_board.count_in_flight(root)
    free = max(0, width - in_flight)
    spawned: list[dict] = []
    for _ in range(free):
        mid = roster.next_member_id(root, prefix=member_prefix)
        task = task_board.claim_top(root, mid, now=now)
        if task is None:
            break  # backlog empty
        spawn_fn(root, member_id=mid, task_id=task["task_id"], cwd=cwd, exec_cmd=exec_cmd)
        spawned.append({"member_id": mid, "task_id": task["task_id"]})
    return {"spawned": spawned, "in_flight": in_flight, "free": free,
            "reassigned": reassigned}


def _should_stop(pool_doc: dict, *, in_flight: int, elapsed: float,
                 lead_ttl: float, max_wall: float, now: float) -> str | None:
    """Return a stop reason, or None to keep coordinating. Never orphan-spawn."""
    if pool_doc.get("state") == "draining" and in_flight == 0:
        return "drained"
    hb = float(pool_doc.get("lead_heartbeat_ts", 0.0))
    if hb > 0 and now - hb > lead_ttl:
        return "lead-heartbeat-stale"
    if elapsed > max_wall:
        return "max-wall"
    return None


def cmd_status(a: argparse.Namespace) -> int:
    root = Path(a.root)
    print(json.dumps({
        "roster": roster.load(root),
        "members": roster.members(root),
        "tasks": task_board.snapshot(root),
    }, ensure_ascii=False))
    return 0


def cmd_wait(a: argparse.Namespace) -> int:
    root = Path(a.root)
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        if task_board.all_done(root):
            print(json.dumps({"done": True}))
            return 0
        time.sleep(a.poll)
    print(json.dumps({"done": task_board.all_done(root)}))
    return 0


def cmd_send(a: argparse.Namespace) -> int:
    mailbox.send(Path(a.root), to=a.to, frm=getattr(a, "from"), text=a.text, now=time.time())
    return 0


def cmd_drain(a: argparse.Namespace) -> int:
    print(json.dumps(mailbox.drain(Path(a.root), a.member_id), ensure_ascii=False))
    return 0


def cmd_claim(a: argparse.Namespace) -> int:
    got = task_board.claim(Path(a.root), a.member_id, now=time.time())
    print(json.dumps(got, ensure_ascii=False))
    return 0


def cmd_reassign(a: argparse.Namespace) -> int:
    ids = task_board.reassign_stale(Path(a.root), ttl=a.ttl, now=time.time())
    print(json.dumps({"reassigned": ids}))
    return 0


def cmd_dissolve(a: argparse.Namespace) -> int:
    root = Path(a.root)
    roster.set_state(root, "dissolved")
    if not a.keep_worktrees and a.repo:
        for m in roster.members(root):
            wt = m.get("worktree")
            if wt:
                worktree.remove(Path(a.repo), Path(wt))
    return 0


def cmd_pool_set(a: argparse.Namespace) -> int:
    doc = pool.update(Path(a.root),
                      width=a.width if a.width is not None else None,
                      state=a.state or None, now=time.time())
    print(json.dumps(doc, ensure_ascii=False))
    return 0


def cmd_coordinate(a: argparse.Namespace) -> int:
    root = Path(a.root)
    cwd = Path(a.cwd) if a.cwd else Path.cwd()
    start = time.time()
    while True:
        now = time.time()
        p = pool.read(root)
        state = p.get("state", "running")
        # during draining, target width 0 so the pool empties instead of refilling
        width = 0 if state == "draining" else int(p.get("width") or a.width)
        in_flight = task_board.count_in_flight(root)
        reason = _should_stop(p, in_flight=in_flight, elapsed=now - start,
                              lead_ttl=a.lead_ttl, max_wall=a.max_wall, now=now)
        if reason:
            print(json.dumps({"stopped": reason, "in_flight": in_flight}))
            return 0
        res = refill_once(root, width=width, cwd=cwd, member_prefix=a.member_prefix,
                          ttl=a.ttl, now=now, exec_cmd=a.exec_cmd)
        if a.once:
            print(json.dumps({"stopped": "once", **res}))
            return 0
        time.sleep(a.poll)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="argus_skill.tools.team")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("form", help="write the shared task board (+roster if --team-id)")
    f.add_argument("--root", required=True)
    f.add_argument("--team-id", default="")
    f.add_argument("--mission", default="")
    f.add_argument("--lead", default="lead")
    f.add_argument("--tasks", required=True)
    f.set_defaults(fn=cmd_form)

    s = sub.add_parser("spawn", help="launch a detached headless teammate engineer")
    s.add_argument("--root", required=True)
    s.add_argument("--team-id", required=True)
    s.add_argument("--member-id", required=True)
    s.add_argument("--task-id", required=True)
    s.add_argument("--cwd", default="", help="where the teammate mission runs (default: cwd)")
    s.add_argument("--repo", default="")
    s.add_argument("--worktree", action="store_true",
                   help="opt-in: run the teammate in an isolated git worktree of --repo")
    s.add_argument("--exec-cmd", default="")
    s.set_defaults(fn=cmd_spawn)

    st = sub.add_parser("status", help="aggregate roster + task board")
    st.add_argument("--root", required=True)
    st.set_defaults(fn=cmd_status)

    w = sub.add_parser("wait", help="block until all tasks done or timeout")
    w.add_argument("--root", required=True)
    w.add_argument("--timeout", type=float, default=3600.0)
    w.add_argument("--poll", type=float, default=2.0)
    w.set_defaults(fn=cmd_wait)

    sd = sub.add_parser("send", help="send a mailbox message")
    sd.add_argument("--root", required=True)
    sd.add_argument("--to", required=True)
    sd.add_argument("--from", required=True)
    sd.add_argument("--text", required=True)
    sd.set_defaults(fn=cmd_send)

    dr = sub.add_parser("drain", help="drain a member's mailbox")
    dr.add_argument("--root", required=True)
    dr.add_argument("--member-id", required=True)
    dr.set_defaults(fn=cmd_drain)

    cl = sub.add_parser("claim", help="claim the next available task")
    cl.add_argument("--root", required=True)
    cl.add_argument("--member-id", required=True)
    cl.set_defaults(fn=cmd_claim)

    ra = sub.add_parser("reassign", help="return stale claimed/running tasks to pending")
    ra.add_argument("--root", required=True)
    ra.add_argument("--ttl", type=float, default=120.0)
    ra.set_defaults(fn=cmd_reassign)

    ds = sub.add_parser("dissolve", help="mark team dissolved + clean worktrees")
    ds.add_argument("--root", required=True)
    ds.add_argument("--repo", default="")
    ds.add_argument("--keep-worktrees", action="store_true")
    ds.set_defaults(fn=cmd_dissolve)

    co = sub.add_parser("coordinate", help="rolling pool: keep N teammates in flight until drained")
    co.add_argument("--root", required=True)
    co.add_argument("--team-id", default="")          # accepted for symmetry/logging
    co.add_argument("--cwd", default="")
    co.add_argument("--width", type=int, default=8)
    co.add_argument("--poll", type=float, default=5.0)
    co.add_argument("--ttl", type=float, default=180.0)         # teammate heartbeat stale
    co.add_argument("--lead-ttl", type=float, default=1800.0)   # lead heartbeat stale (30min: the daemon lead is a sequence of bounded, read-heavy missions, not a continuous heartbeater)
    co.add_argument("--max-wall", type=float, default=21600.0)  # 6h backstop
    co.add_argument("--member-prefix", default="w")
    co.add_argument("--once", action="store_true", help="run a single refill tick and exit")
    co.add_argument("--exec-cmd", default="")                   # test stub
    co.set_defaults(fn=cmd_coordinate)

    ps = sub.add_parser("pool-set", help="set coordinator width/state (+ lead heartbeat)")
    ps.add_argument("--root", required=True)
    ps.add_argument("--width", type=int, default=None)
    ps.add_argument("--state", default="", choices=["", "running", "draining"])
    ps.set_defaults(fn=cmd_pool_set)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
