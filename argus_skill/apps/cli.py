"""argus-skill CLI — single-entry 7×24 lifetime agent.

The product has exactly one positioning: a long-running supervised
coding agent that drains a backlog forever. There is therefore exactly
one entry point — ``argus-skill`` — which:

* drops you into the unified life REPL (the cockpit), and
* by default ensures a detached daemon is alive draining the backlog
  in the background even after you log out.

Top-level flags control daemon lifecycle and read-only operator help
(``--daemon``, ``--daemon-fg``, ``--daemon-stop``, ``--status``,
``--daemon-runbook``, ``--no-daemon``). There are no other subcommands
— earlier ad-hoc ``run`` / ``list-skills`` modes were removed because
they fragmented the mental model and competed with the backlog-driven
workflow.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    from .. import __version__

    parser = argparse.ArgumentParser(
        prog="argus-skill",
        description="argus-skill — 7×24 supervised lifetime coding agent",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"argus-skill {__version__}",
    )

    daemon_grp = parser.add_argument_group("7×24 daemon")
    daemon_grp.add_argument(
        "--daemon",
        action="store_true",
        help="start a detached background worker that drains the backlog forever",
    )
    daemon_grp.add_argument(
        "--daemon-fg",
        action="store_true",
        help="run the worker in the foreground (for systemd / debugging)",
    )
    daemon_grp.add_argument(
        "--daemon-stop",
        action="store_true",
        help="send SIGTERM to a running daemon for this life-dir",
    )
    daemon_grp.add_argument(
        "--status",
        action="store_true",
        help="print daemon + backlog status and exit (no REPL)",
    )
    daemon_grp.add_argument(
        "--daemon-runbook",
        action="store_true",
        help="print the daemon-safe upgrade / restart playbook and exit",
    )
    daemon_grp.add_argument(
        "--no-daemon",
        action="store_true",
        help="skip auto-spawning the background daemon when entering the REPL",
    )
    daemon_grp.add_argument(
        "--life-dir",
        default=None,
        help="override life state directory (default: ~/.argus-skill/life)",
    )
    daemon_grp.add_argument(
        "--continuous",
        action="store_true",
        help="enable continuous planner mode (daemon generates new tasks "
             "when backlog is empty)",
    )
    daemon_grp.add_argument(
        "--objective",
        default="",
        help="continuous improvement objective (used with --continuous)",
    )

    cockpit_grp = parser.add_argument_group("cockpit")
    cockpit_grp.add_argument(
        "--watch",
        action="store_true",
        help="open the live read-only cockpit (mission/events/journal/backlog)",
    )
    cockpit_grp.add_argument(
        "--notify",
        metavar="MSG",
        help="append a nudge message to the supervisor's inbox (the next "
             "engineer round picks it up as operator guidance)",
    )
    cockpit_grp.add_argument(
        "--init-identity",
        action="store_true",
        help="run the interactive identity-card wizard "
             "(never overwrites an existing card)",
    )

    skills_grp = parser.add_argument_group("skill admin")
    skills_grp.add_argument(
        "--skill-stats",
        action="store_true",
        help="print empirical skill effectiveness report (hit-rate, "
             "mean rounds with/without skill) and exit",
    )
    skills_grp.add_argument(
        "--skill-stats-json",
        action="store_true",
        help="render the skill-stats output as JSON instead of plain text",
    )
    skills_grp.add_argument(
        "--skill-cleanse",
        action="store_true",
        help="strip historic 'Memory context' boilerplate from existing skill "
             "task_history entries (idempotent migration)",
    )
    skills_grp.add_argument(
        "--skill-compact",
        action="store_true",
        help="cluster near-duplicate skills and propose archiving redundant "
             "ones; pass --apply to actually archive (otherwise dry-run)",
    )
    skills_grp.add_argument(
        "--apply",
        action="store_true",
        help="with --skill-compact / --skill-cleanse: actually mutate disk "
             "(default is dry-run)",
    )
    skills_grp.add_argument(
        "--sim-threshold",
        type=float,
        default=None,
        help="cosine-similarity threshold for --skill-compact clustering "
             "(default 0.55)",
    )
    skills_grp.add_argument(
        "--skills-dir",
        default=None,
        help="override skills directory (default: ~/.argus-skill/skills)",
    )

    return parser


def _continuous_contract_error(
    *,
    continuous: bool,
    objective: str,
    backend: str,
) -> str:
    from ..daemon.life_worker import continuous_mode_error
    return continuous_mode_error(backend, continuous, objective)



def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.skill_stats = bool(args.skill_stats or args.skill_stats_json)
    backend_default = os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex")
    continuous_error = _continuous_contract_error(
        continuous=bool(args.continuous),
        objective=str(getattr(args, "objective", "") or ""),
        backend=backend_default,
    )
    if continuous_error:
        sys.stderr.write(f"argus-skill: {continuous_error}\n")
        return 2

    # ---- mutual exclusion -----------------------------------------
    # Action-style flags pick exactly one mission; --no-daemon and
    # --life-dir are modifiers and may combine with any of them.
    action_flags = (
        bool(args.daemon)
        + bool(args.daemon_fg)
        + bool(args.daemon_stop)
        + bool(args.status)
        + bool(args.daemon_runbook)
        + bool(args.watch)
        + bool(args.notify)
        + bool(args.init_identity)
        + bool(args.skill_stats)
        + bool(args.skill_cleanse)
        + bool(args.skill_compact)
    )
    if action_flags > 1:
        sys.stderr.write(
            "argus-skill: --daemon / --daemon-fg / --daemon-stop / --status / "
            "--daemon-runbook / --watch / --notify / --init-identity / "
            "--skill-stats / --skill-cleanse / --skill-compact are mutually "
            "exclusive.\n"
        )
        return 2
    if args.daemon:
        return _cmd_daemon_start(args, foreground=False)
    if args.daemon_fg:
        return _cmd_daemon_start(args, foreground=True)
    if args.daemon_stop:
        return _cmd_daemon_stop(args)
    if args.status:
        return _cmd_status(args)
    if args.daemon_runbook:
        return _cmd_daemon_runbook(args)
    if args.watch:
        return _cmd_watch(args)
    if args.notify:
        return _cmd_notify(args)
    if args.init_identity:
        return _cmd_init_identity(args)
    if args.skill_stats:
        return _cmd_skill_stats(args)
    if args.skill_cleanse:
        return _cmd_skill_cleanse(args)
    if args.skill_compact:
        return _cmd_skill_compact(args)

    # Default path: drop into the unified life REPL. The REPL itself
    # auto-spawns a background daemon (unless ``--no-daemon`` was given
    # or one is already alive) so the agent keeps draining the backlog
    # 24/7 even after the operator detaches.
    from ._life_repl import run_life_chat_loop

    repl_args = argparse.Namespace(
        life_dir=args.life_dir,
        color=None,
        backend=backend_default,
        scientist_model=os.environ.get("ARGUS_SKILL_SCIENTIST_MODEL", "gpt-5.4"),
        engineer_model=os.environ.get("ARGUS_SKILL_ENGINEER_MODEL",
                                      "gpt-5.4-mini"),
        reviewer_model=os.environ.get("ARGUS_SKILL_REVIEWER_MODEL"),
        plan_mode="auto",
        plan_model=None,
        max_rounds=500,
        check=[],
        workdir=None,
        no_daemon=bool(args.no_daemon),
        continuous=bool(args.continuous),
        objective=str(getattr(args, "objective", "") or ""),
    )
    return run_life_chat_loop(repl_args)


# ---------------------------------------------------------------------------
# 7×24 daemon dispatchers
# ---------------------------------------------------------------------------

def _resolve_life_dir(args: argparse.Namespace) -> Path:
    from ..life.memory import default_life_dir
    if args.life_dir:
        return Path(args.life_dir).expanduser()
    return default_life_dir()


def _build_worker_config(args: argparse.Namespace):
    from ..daemon.life_worker import LifeWorkerConfig
    backend = getattr(args, "backend", None) or os.environ.get(
        "ARGUS_SKILL_LIFE_BACKEND",
        "codex",
    )
    return LifeWorkerConfig(
        life_dir=_resolve_life_dir(args),
        backend=backend,
        engineer_model=os.environ.get("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini"),
        reviewer_model=os.environ.get("ARGUS_SKILL_REVIEWER_MODEL", "gpt-5.4"),
        scientist_model=os.environ.get("ARGUS_SKILL_SCIENTIST_MODEL", "gpt-5.4"),
        per_mission_cap_usd=float(os.environ.get("ARGUS_SKILL_PER_MISSION_CAP_USD", "30.0")),
        daily_cap_usd=float(os.environ.get("ARGUS_SKILL_DAILY_CAP_USD", "180.0")),
        poll_interval=float(os.environ.get("ARGUS_SKILL_DAEMON_POLL_S", "5.0")),
        continuous=getattr(args, "continuous", False),
        continuous_objective=getattr(args, "objective", ""),
    )


def _cmd_daemon_start(args: argparse.Namespace, *, foreground: bool) -> int:
    from ..daemon.life_worker import run_foreground, spawn_detached_daemon
    backend_default = os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex")
    continuous_error = _continuous_contract_error(
        continuous=bool(getattr(args, "continuous", False)),
        objective=str(getattr(args, "objective", "") or ""),
        backend=backend_default,
    )
    if continuous_error:
        sys.stderr.write(f"argus-skill: {continuous_error}\n")
        return 2
    cfg = _build_worker_config(args)
    if foreground:
        return run_foreground(cfg)
    return spawn_detached_daemon(cfg)


def _cmd_daemon_stop(args: argparse.Namespace) -> int:
    from ..daemon.life_worker import stop_daemon
    return stop_daemon(_resolve_life_dir(args))


def _cmd_watch(args: argparse.Namespace) -> int:
    from ._watch import run_watch
    return run_watch(_resolve_life_dir(args))


def _cmd_notify(args: argparse.Namespace) -> int:
    """Append a free-form nudge to ``<life_dir>/inbox.jsonl``.

    The next engineer round picks it up via the supervisor's
    ``user_inbox`` callable and splices it into the prompt as
    operator guidance.
    """
    import json
    import time
    msg = (args.notify or "").strip()
    if not msg:
        sys.stderr.write("argus-skill: --notify requires a non-empty message\n")
        return 2
    life_dir = _resolve_life_dir(args)
    life_dir.mkdir(parents=True, exist_ok=True)
    inbox = life_dir / "inbox.jsonl"
    record = {"ts": time.time(), "text": msg}
    with inbox.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"argus-skill: queued nudge ({len(msg)} chars) → {inbox}")
    return 0


def _cmd_init_identity(args: argparse.Namespace) -> int:
    from ._init_identity import run_init_identity
    return run_init_identity(_resolve_life_dir(args))


def _resolve_skills_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "skills_dir", None):
        return Path(args.skills_dir).expanduser()
    # Default: <life_dir>/../skills, matching _life_repl + life_worker.
    life_dir = _resolve_life_dir(args)
    return life_dir.parent / "skills"


def _cmd_skill_stats(args: argparse.Namespace) -> int:
    from ._skill_stats import run_skill_stats
    return run_skill_stats(
        _resolve_life_dir(args),
        as_json=bool(args.skill_stats_json),
    )


def _cmd_skill_cleanse(args: argparse.Namespace) -> int:
    from ._skill_cleanse import run_cleanse
    return run_cleanse(
        _resolve_skills_dir(args),
        dry_run=not bool(args.apply),
    )


def _cmd_skill_compact(args: argparse.Namespace) -> int:
    from ..scientist.compactor import DEFAULT_SIM_THRESHOLD, run_compact
    threshold = (
        float(args.sim_threshold)
        if args.sim_threshold is not None
        else DEFAULT_SIM_THRESHOLD
    )
    return run_compact(
        _resolve_skills_dir(args),
        sim_threshold=threshold,
        dry_run=not bool(args.apply),
    )


def _cmd_status(args: argparse.Namespace) -> int:
    from ..daemon.life_worker import read_continuous_state, read_daemon_status
    from ..life.memory import LifeMemory
    life_dir = _resolve_life_dir(args)
    status = read_daemon_status(life_dir)
    mem = LifeMemory.open(life_dir)
    all_items = mem.backlog.all()
    pending = sum(1 for it in all_items if it.status == "pending")
    running = sum(1 for it in all_items if it.status == "running")
    done = sum(1 for it in all_items if it.status == "done")
    failed = sum(1 for it in all_items if it.status == "failed")
    skipped = sum(1 for it in all_items if it.status == "skipped")
    # Status should stay cheap even on a long-lived daemon.
    journal_tail = mem.journal.tail(3)

    print(f"argus-skill — life-dir: {life_dir}")
    if status.alive and status.pid is not None:
        uptime = _format_short_duration(status.uptime_seconds or 0.0)
        backend = status.backend or "?"
        print(f"  daemon   : alive (pid {status.pid}, up {uptime}, backend {backend})")
    else:
        print("  daemon   : not running   (start with `argus-skill --daemon`)")
    parts = [f"{pending} pending", f"{done} done", f"{failed} failed"]
    if running:
        parts.insert(1, f"{running} running ⚠")
    if skipped:
        parts.append(f"{skipped} skipped")
    print(f"  backlog  : {' · '.join(parts)}")
    # Total cost from journal
    try:
        total_cost = mem.journal.total_cost_since(0)
        print(f"  cost     : ${total_cost:.2f} total")
    except Exception:  # noqa: BLE001
        pass
    if running and not (status.alive and status.pid is not None):
        print(
            "             ↳ orphan running items will be reaped to `failed` "
            "when a worker (REPL or --daemon) next starts."
        )
    cont = read_continuous_state(life_dir)
    print(f"  continuous: {'on' if cont.enabled else 'off'}")
    if cont.objective:
        print(f"    objective: {cont.objective}")
    if cont.done_reason:
        print(f"    done_reason: {cont.done_reason}")
    if cont.done_at:
        print(f"    done_at: {cont.done_at}")
    if journal_tail:
        print("  recent   :")
        for entry in journal_tail:
            print(f"    - {entry.kind}  {entry.summary}")
    survival_msg = _check_logout_survival(status)
    if survival_msg:
        print(f"  survival : {survival_msg}")
    return 0


def _cmd_daemon_runbook(args: argparse.Namespace) -> int:
    life_dir = _resolve_life_dir(args)
    from ..daemon.life_worker import read_daemon_status

    status = read_daemon_status(life_dir)
    lines = [
        "argus-skill daemon-safe upgrade runbook",
        f"life-dir : {life_dir}",
        (
            f"daemon   : alive (pid {status.pid})"
            if status.alive and status.pid is not None
            else "daemon   : not running"
        ),
        "",
        "1. Open a second shell, tmux pane, or systemd session before touching the daemon.",
        "2. Treat the live daemon as the control plane: do not restart the process that owns your current session.",
        "3. Persist context first. The backlog, journal, inbox, and skills already live on disk under the life-dir root.",
        "4. For an ad-hoc detached worker, run `argus-skill --daemon-stop` from the external shell, wait for exit, update the code, then relaunch with `argus-skill --daemon`.",
        "5. For a systemd-managed worker, edit the unit from the maintenance shell, then run `systemctl daemon-reload && systemctl restart argus-skill.service`.",
        "6. Verify the new process with `argus-skill --status` before resuming work.",
    ]
    print("\n".join(lines))
    return 0


def _check_logout_survival(status) -> str | None:  # noqa: ANN001
    """Best-effort check whether the daemon will survive logout.

    The daemon already double-forks + setsid + ignores SIGHUP, so an
    SSH disconnect or terminal close cannot kill it. The remaining
    real-world risk on Linux is ``systemd-logind KillUserProcesses=yes``
    which kills user-owned processes (regardless of session) when the
    user has no more login sessions and ``linger`` is off. We probe
    ``loginctl show-user`` and tell the operator how to fix it.
    """
    if not (status.alive and status.pid is not None):
        return None
    if sys.platform != "linux":
        return None
    try:
        import getpass
        import subprocess
        user = getpass.getuser()
        out = subprocess.run(
            ["loginctl", "show-user", user, "--property=Linger"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    body = (out.stdout or "").strip()
    if "Linger=yes" in body:
        return "linger=on  (daemon will survive logout / SSH disconnect)"
    if "Linger=no" in body:
        return (
            "linger=off ⚠  daemon may be killed at logout. "
            f"Run `loginctl enable-linger {getpass.getuser()}` to make 7×24 honest."
        )
    return None


def _format_short_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s}s"
    if seconds < 86400:
        h, rem = divmod(int(seconds), 3600)
        m, _ = divmod(rem, 60)
        return f"{h}h {m}m"
    d, rem = divmod(int(seconds), 86400)
    h, _ = divmod(rem, 3600)
    return f"{d}d {h}h"
