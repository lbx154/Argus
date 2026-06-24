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
    if a.team_id:
        roster.create(Path(a.root), team_id=a.team_id, mission=a.mission,
                      lead=a.lead, now=time.time())
    task_board.form(Path(a.root), _load_tasks(Path(a.tasks)))
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


def _pid_alive(pid: object) -> bool:
    """True if ``pid`` names a live process (signal 0 probes without killing)."""
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True  # exists but owned by another user — still alive
    return True


def _proc_cmdline(pid: int) -> str | None:
    """Best-effort process cmdline as a token list joined by spaces (Linux
    ``/proc``). ``None`` when it can't be read (non-Linux, gone, no perm)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except (OSError, ValueError):
        return None


def _member_pid_alive(member: dict) -> bool:
    """True if this member's teammate process is genuinely still running.

    A bare ``os.kill(pid, 0)`` is NOT enough: a long campaign churns thousands
    of short-lived teammates, the roster never prunes, and the OS recycles dead
    PIDs onto unrelated processes. Counting a recycled PID as live made the
    coordinator believe the pool was full and stop refilling (observed: 96
    "alive" PIDs but only 55 real teammates → pool stuck at ~57/96). So when the
    cmdline is readable we require it to be THIS member's teammate process
    (``teammate_entry`` launched with the member's exact ``--member-id``); where
    the cmdline can't be introspected we fall back to the liveness probe alone.
    """
    pid = member.get("pid")
    if not pid or not _pid_alive(pid):
        return False
    cmdline = _proc_cmdline(int(pid))
    if cmdline is None:
        return True  # can't introspect (non-Linux) — trust the liveness probe
    if "teammate_entry" not in cmdline:
        return False  # recycled onto an unrelated process
    toks = cmdline.split()
    mid = str(member.get("id", ""))
    return any(toks[i] == "--member-id" and i + 1 < len(toks) and toks[i + 1] == mid
               for i in range(len(toks)))


def _live_member_ids(root: Path) -> set[str]:
    """Roster member ids whose teammate process is genuinely alive."""
    return {
        str(m["id"])
        for m in roster.members(root)
        if m.get("id") and _member_pid_alive(m)
    }


def _count_live_members(root: Path) -> int:
    """Number of roster members whose teammate process is genuinely alive.

    ``task_board.count_in_flight`` lags reality: a freshly spawned teammate
    takes seconds to register its first heartbeat, and a teammate that dies
    *without* cleanly failing its task leaves that task ``claimed``. Sizing the
    pool on the board alone therefore lets the coordinator spawn a thundering
    herd on top of teammates that are already running (observed: width 8 →
    49 live processes). Counting verified live PIDs is process-accurate, so the
    pool is sized by how many teammates are ACTUALLY running.
    """
    return len(_live_member_ids(root))


def refill_once(root: Path, *, width: int, cwd: Path, member_prefix: str = "w",
                ttl: float, now: float, exec_cmd: str = "", spawn_fn=None) -> dict:
    """Top the in-flight teammate count back up to ``width`` from the backlog.

    Reassign stale (dead) teammates' tasks first, then claim the highest-priority
    pending tasks and spawn a fresh teammate on each until the pool is full or the
    backlog is empty. Idempotent: a second call with the pool already full spawns
    nothing. ``spawn_fn`` is injectable for tests.

    Occupancy is the MAX of the board's in-flight count and the live teammate
    PID count, so a spawn that hasn't registered yet (or a teammate that died
    without failing its task) can never be mistaken for a free slot — this is
    what prevents the over-spawn herd. ``ARGUS_TEAM_MAX_SPAWN_PER_REFILL`` caps
    how many launch per call (0 = uncapped, back-compat) to smooth the startup
    load when a large pool cold-fills.
    """
    spawn_fn = spawn_fn or _spawn_teammate
    # (1) Hand stale-owned tasks back only when their owner process is not live.
    #     A heartbeat can lag while the teammate is still alive; resetting that
    #     task to pending creates duplicate live teammates for the same task.
    live_owner_ids = _live_member_ids(root)
    reassigned = task_board.reassign_stale(
        root, ttl=ttl, now=now, live_owners=live_owner_ids)
    # (2) Two occupancy signals, each able to lag the other:
    #       in_flight = tasks claimed/running on the board — can OVER-count when a
    #                   teammate died without failing its task (stuck "claimed");
    #       live      = roster members whose process is verified alive — can
    #                   over-count vs the board when a just-spawned teammate hasn't
    #                   registered its first heartbeat yet.
    #     Taking the MAX means a slot counts as free only when BOTH agree it is, so
    #     we never spawn on top of a teammate that is already running (the herd).
    in_flight = task_board.count_in_flight(root)
    live = len(live_owner_ids)
    occupied = max(in_flight, live)
    free = max(0, width - occupied)
    # (3) Optional per-refill spawn cap. Even with accurate occupancy a cold pool
    #     (occupied≈0, large width) would launch `width` processes in one burst — a
    #     torch-import thundering herd. Capping ramps the pool up over several polls.
    cap = int(os.environ.get("ARGUS_TEAM_MAX_SPAWN_PER_REFILL", "0") or 0)
    if cap > 0:
        free = min(free, cap)
    # (4) One teammate per free slot: claim the top-priority pending task (claim_top
    #     marks it claimed synchronously, so the next poll's in_flight already
    #     reflects it) and launch the teammate on it. Stop early if the backlog dries.
    spawned: list[dict] = []
    for _ in range(free):
        mid = roster.next_member_id(root, prefix=member_prefix)
        task = task_board.claim_top(root, mid, now=now)
        if task is None:
            break  # backlog empty
        spawn_fn(root, member_id=mid, task_id=task["task_id"], cwd=cwd, exec_cmd=exec_cmd)
        spawned.append({"member_id": mid, "task_id": task["task_id"]})
    return {"spawned": spawned, "in_flight": in_flight, "live": live,
            "occupied": occupied, "free": free, "reassigned": reassigned}


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
