"""Agent-facing CLI for Argus Agent Teams (the lead and teammates call this).

Thin wrapper over argus_skill.team.{task_board,mailbox,roster,worktree}. The
rolling pool is no longer driven by a detached ``coordinate`` loop — a
daemon-resident **Curator** keeps N teammates in flight. The lead's only pool
levers here are ``form`` (writes the backlog + a campaign marker the Curator
discovers) and ``pool-set`` (width/state intent). ``spawn`` remains as a manual
escape hatch; tests override the launched command with ``--exec-cmd``.

Verbs: form / spawn / status / wait / send / drain / claim / reassign /
dissolve / pool-set.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from ..team import mailbox, pool, registry, roster, task_board, worktree


def _load_tasks(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def cmd_form(a: argparse.Namespace) -> int:
    root = Path(a.root)
    if a.team_id:
        roster.create(root, team_id=a.team_id, mission=a.mission,
                      lead=a.lead, now=time.time())
    task_board.form(root, _load_tasks(Path(a.tasks)))
    # Drop a campaign marker so the daemon-resident Curator discovers this team
    # and keeps its pool in flight. Only inside a project (the daemon exports
    # ARGUS_SKILL_PROJECT_ROOT) and only for a team with an id.
    project_root = os.environ.get("ARGUS_SKILL_PROJECT_ROOT", "")
    if project_root and a.team_id:
        registry.write_marker(Path(project_root), team_id=a.team_id,
                              team_root=root, cwd=(a.cwd or os.getcwd()),
                              now=time.time())
    return 0


def _spawn_teammate(root: Path, *, member_id: str, task_id: str, cwd: Path,
                    exec_cmd: str = "") -> int:
    """Launch ONE headless teammate on ``task_id`` (manual escape hatch), record
    it on the roster, return its pid. The rolling pool is driven by the Curator;
    this is for one-off / test spawns. Claiming is the caller's job."""
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
        "task_id": task_id, "status": "running",
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


def _live_member_ids(root: Path) -> set[str]:
    """Member ids whose teammate_entry process is alive for THIS root, so a
    reassign never resets a still-running teammate's task on a merely-stalled
    heartbeat (the live-owner guard reassign_stale exists for is otherwise inert
    on this CLI path). Best-effort: any discovery error → empty set."""
    try:
        out = subprocess.run(
            ["pgrep", "-af", "argus_skill.team.teammate_entry"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:  # noqa: BLE001 — discovery is best-effort, never block reassign
        return set()
    rootstr = str(root)
    ids: set[str] = set()
    for line in out.splitlines():
        if f"--root {rootstr}" not in line:
            continue
        m = re.search(r"--member-id (\S+)", line)
        if m:
            ids.add(m.group(1))
    return ids


def cmd_reassign(a: argparse.Namespace) -> int:
    ids = task_board.reassign_stale(
        Path(a.root), ttl=a.ttl, now=time.time(),
        live_owners=_live_member_ids(Path(a.root)),
    )
    print(json.dumps({"reassigned": ids}))
    return 0


def cmd_dissolve(a: argparse.Namespace) -> int:
    root = Path(a.root)
    roster.set_state(root, "dissolved")
    # Tell the resident Curator to wind this campaign down (stop refilling and
    # drop the campaign marker once nothing is in flight).
    pool.update(root, state="dissolved")
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


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="argus_skill.tools.team")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("form", help="write the shared task board (+roster if --team-id) and a campaign marker")
    f.add_argument("--root", required=True)
    f.add_argument("--team-id", default="")
    f.add_argument("--mission", default="")
    f.add_argument("--lead", default="lead")
    f.add_argument("--tasks", required=True)
    f.add_argument("--cwd", default="", help="where teammates run (recorded in the campaign marker)")
    f.set_defaults(fn=cmd_form)

    s = sub.add_parser("spawn", help="launch a manual headless teammate engineer (escape hatch)")
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

    ps = sub.add_parser("pool-set", help="set pool width/state (the lead's intent for the Curator)")
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
