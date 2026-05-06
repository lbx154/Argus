"""``argus-skill up`` — one-line launcher for the merged queue+mission daemon.

Why this exists
---------------

The merged queue+mission daemon (``argus-skill daemon --engine mission``)
needs a handful of flags + env vars to run:

  ARGUS_SKILL_BACKEND=codex ARGUS_SKILL_RUNNER_BACKEND=codex \
    ARGUS_SKILL_RUNNER_BIN=$(which codex) \
    argus-skill daemon \
      --state-dir ... --skills-dir ... --workdir ... \
      --engine mission --mission-max-rounds N --mission-plan-mode off \
      --max-follow-ups 1 --no-telegram --no-token-lock

That's a lot. ``argus-skill up`` collapses it to:

  $ argus-skill up
  🚀 daemon up at ~/.argus-skill/state (engine=mission, codex backend)
  > /run 给我讲个笑话
  ...

Defaults applied (override with the named flags):

  * ``--state-dir``         = ``~/.argus-skill/state``
  * ``--workdir``           = ``~/.argus-skill/work``
  * ``--skills-dir``        = first existing of:
      $ARGUS_SKILL_SKILLS_DIR, ./skills, ~/.argus-skill/skills,
      /home/argustest/argus-skill/skills
  * ``--engine``            = ``mission`` (the merged engine; pass
                              ``--legacy`` for the old skill-loop)
  * ``--max-rounds``        = 3
  * ``--plan-mode``         = ``off``
  * ``--max-follow-ups``    = 1
  * backend                 = ``codex`` if ``codex`` on PATH else
                              ``copilot`` if ``copilot`` on PATH else
                              ``memory`` (deterministic stub)
  * Telegram + token-lock   = off (single-user local mode)

Unlike ``go`` (which creates a mission upfront), ``up`` enters chat
without an initial task — you ``/run`` tasks ad-hoc through the queue.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from ..daemon.bus import BusCommand, JsonlCommandBus, inspect_daemon_status

DEFAULT_STATE_DIR = "~/.argus-skill/state"
DEFAULT_WORKDIR = "~/.argus-skill/work"
NODE_BIN_PATH = "/home/argustest/.nvm/versions/node/v22.22.0/bin"


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def add_up_subcommand(sub: argparse._SubParsersAction) -> None:
    up_p = sub.add_parser(
        "up",
        help=(
            "one-line launcher: spawn the merged queue+mission daemon with "
            "sensible defaults, then attach the chat REPL"
        ),
    )
    up_p.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    up_p.add_argument("--workdir", default=DEFAULT_WORKDIR)
    up_p.add_argument("--skills-dir", default=None,
                      help="default: first existing of env / ./skills / ~/.argus-skill/skills")
    up_p.add_argument("--engine", choices=("mission", "skill-loop"), default="mission",
                      help="mission = full LoopEngine + skill loop (default); skill-loop = legacy")
    up_p.add_argument("--max-rounds", type=int, default=3)
    up_p.add_argument("--plan-mode", choices=("auto", "off", "record"), default="off")
    up_p.add_argument("--max-follow-ups", type=int, default=1)
    up_p.add_argument(
        "--auto-follow-up",
        dest="auto_follow_up",
        action="store_true",
        default=True,
    )
    up_p.add_argument(
        "--no-auto-follow-up",
        dest="auto_follow_up",
        action="store_false",
    )
    up_p.add_argument(
        "--enable-final-report",
        dest="enable_final_report",
        action="store_true",
        default=False,
        help="enable post-task final-report.md (default: off)",
    )
    up_p.add_argument(
        "--enable-pptx-report",
        dest="enable_pptx_report",
        action="store_true",
        default=False,
        help="enable post-task PPTX report (default: off)",
    )
    up_p.add_argument(
        "--backend",
        choices=("auto", "codex", "copilot", "memory"),
        default="auto",
        help="force a backend instead of auto-detecting",
    )
    up_p.add_argument("--check", action="append", default=[])
    up_p.add_argument(
        "--attach-only",
        action="store_true",
        help="don't spawn a daemon; just attach to one already running at --state-dir",
    )
    up_p.add_argument(
        "--restart",
        action="store_true",
        help="if a daemon is already running at --state-dir, kill it and spawn a fresh one",
    )
    up_p.add_argument(
        "--no-auto-restart",
        action="store_true",
        help="disable the default behaviour of auto-restarting stale/incompatible daemons (e.g. wrong engine, missing live-progress wiring)",
    )
    up_p.add_argument(
        "--shutdown-timeout",
        type=int,
        default=30,
    )
    up_p.add_argument("--quiet", action="store_true")
    up_p.add_argument("--color", dest="color", action="store_true", default=None)
    up_p.add_argument("--no-color", dest="color", action="store_false")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_skills_dir(explicit: str | None) -> str:
    if explicit:
        return explicit
    candidates = [
        os.environ.get("ARGUS_SKILL_SKILLS_DIR"),
        "./skills",
        os.path.expanduser("~/.argus-skill/skills"),
        "/home/argustest/argus-skill/skills",
    ]
    for c in candidates:
        if c and Path(c).is_dir():
            return c
    fallback = os.path.expanduser("~/.argus-skill/skills")
    Path(fallback).mkdir(parents=True, exist_ok=True)
    return fallback


def _detect_backend(forced: str) -> tuple[str, str | None]:
    """Return (backend_name, runner_bin_path)."""
    if forced != "auto":
        if forced in ("codex", "copilot"):
            bin_path = shutil.which(forced)
            return forced, bin_path
        return forced, None
    # auto
    for name in ("codex", "copilot"):
        bin_path = shutil.which(name)
        if bin_path:
            return name, bin_path
    # ensure node bin path is searched too
    extra = Path(NODE_BIN_PATH)
    if extra.is_dir():
        for name in ("codex", "copilot"):
            cand = extra / name
            if cand.is_file():
                return name, str(cand)
    return "memory", None


def _is_daemon_alive(state_dir: Path) -> tuple[bool, int | None]:
    status = state_dir / "status.json"
    if not status.is_file():
        return False, None
    inspection = inspect_daemon_status(str(status), stale_after_seconds=15)
    return inspection.is_live, inspection.daemon_pid


def _running_daemon_engine(state_dir: Path) -> str | None:
    """Return the engine value recorded in status.json, or None if absent.

    Daemons started before the queue+mission merge don't write the
    ``engine`` field — that's our cue to restart, since they also lack
    the live-progress wiring (per-line ``engineer.progress`` events).
    """
    status = state_dir / "status.json"
    try:
        payload = json.loads(status.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    eng = payload.get("engine")
    return str(eng) if isinstance(eng, str) and eng else None


def _kill_daemon(pid: int, *, timeout: float = 10.0) -> bool:
    """SIGTERM then SIGKILL the daemon. Returns True if it died."""
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    time.sleep(0.5)
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True


def _spawn_daemon(
    *,
    state_dir: Path,
    workdir: Path,
    skills_dir: str,
    engine: str,
    max_rounds: int,
    plan_mode: str,
    max_follow_ups: int,
    auto_follow_up: bool,
    enable_final_report: bool,
    enable_pptx_report: bool,
    checks: list[str],
    backend: str,
    backend_bin: str | None,
    log_file: Path,
) -> subprocess.Popen:
    env = os.environ.copy()
    env["ARGUS_SKILL_BACKEND"] = backend
    env["ARGUS_SKILL_RUNNER_BACKEND"] = backend
    if backend_bin:
        env["ARGUS_SKILL_RUNNER_BIN"] = backend_bin
    if NODE_BIN_PATH not in env.get("PATH", ""):
        env["PATH"] = f"{NODE_BIN_PATH}:{env.get('PATH', '')}"

    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log_file, "ab", buffering=0)

    cmd = [
        sys.executable, "-m", "argus_skill", "daemon",
        "--state-dir", str(state_dir),
        "--workdir", str(workdir),
        "--skills-dir", skills_dir,
        "--engine", engine,
        "--mission-max-rounds", str(max_rounds),
        "--mission-plan-mode", plan_mode,
        "--max-follow-ups", str(max_follow_ups),
        "--no-telegram",
        "--no-token-lock",
    ]
    if auto_follow_up:
        cmd.append("--auto-follow-up")
    else:
        cmd.append("--no-auto-follow-up")
    if enable_final_report:
        cmd.append("--enable-final-report")
    if enable_pptx_report:
        cmd.append("--enable-pptx-report")
    for c in checks:
        cmd.extend(["--mission-check", c])

    return subprocess.Popen(
        cmd,
        stdout=fh,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
        cwd=str(workdir),
    )


def _wait_for_daemon_up(
    state_dir: Path,
    *,
    timeout: float = 30.0,
    expect_pid: int | None = None,
    spawn_time: float | None = None,
) -> bool:
    """Wait for the spawned daemon to publish a *fresh* status.json.

    Old code only checked ``daemon_running`` which could be stale from a
    previous daemon's exit-write (or a crashed daemon that never updated
    it). We now require status.json to be modified *after* the spawn time
    AND, if provided, to advertise the pid we just spawned. This avoids
    false negatives during slow imports (status.json gets written within
    the first second of `daemon` cmd entry, but old daemons may take a
    moment to fully exit).
    """
    deadline = time.monotonic() + timeout
    status = state_dir / "status.json"
    while time.monotonic() < deadline:
        # Bail early if the spawned daemon already crashed.
        if expect_pid is not None:
            try:
                os.kill(expect_pid, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                pass
        if status.is_file():
            try:
                st = status.stat()
                fresh = spawn_time is None or st.st_mtime >= spawn_time - 0.1
                payload = json.loads(status.read_text())
                if fresh and payload.get("daemon_running"):
                    if expect_pid is None:
                        return True
                    pid = payload.get("daemon_pid") or payload.get("pid")
                    if pid in (None, expect_pid) or int(pid) == expect_pid:
                        return True
            except (json.JSONDecodeError, OSError, ValueError, TypeError):
                pass
        time.sleep(0.2)
    return False


def _send_stop(state_dir: Path) -> None:
    inbox = state_dir / "inbox.jsonl"
    try:
        bus = JsonlCommandBus(str(inbox))
        bus.publish(BusCommand(kind="stop", text="", source="up", ts=time.time()))
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"(failed to publish /stop: {exc})\n")


def _shutdown_daemon(proc: subprocess.Popen, *, state_dir: Path, timeout: int) -> None:
    if proc.poll() is not None:
        return
    sys.stdout.write("🛑 stopping daemon...\n")
    sys.stdout.flush()
    _send_stop(state_dir)
    deadline = time.monotonic() + timeout
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.5)
    if proc.poll() is not None:
        sys.stdout.write("✅ daemon exited cleanly\n")
        return
    sys.stdout.write(f"⚠️  still alive after {timeout}s — SIGTERM\n")
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.2)
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=3)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def cmd_up(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir).expanduser().resolve()
    workdir = Path(args.workdir).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    skills_dir = _resolve_skills_dir(args.skills_dir)

    from .. import __version__ as _argus_version
    from ..cli.branding import render_startup_banner
    from ..cli.theme import Theme as _Theme
    theme = _Theme.auto(force=getattr(args, "color", None))
    sys.stdout.write(render_startup_banner(
        theme=theme,
        version=_argus_version,
        show_hint=False,
    ))
    sys.stdout.flush()

    alive, pid = _is_daemon_alive(state_dir)
    daemon_proc: subprocess.Popen | None = None

    # Auto-restart: a daemon written by a release that predates the
    # queue+mission merge has no ``engine`` field in status.json and is
    # missing the live-progress wiring; the chat REPL would silently
    # show only lifecycle events with no main-agent stream. Restart it
    # by default unless the user explicitly opts out.
    auto_restart_reason: str | None = None
    if alive and not args.attach_only:
        running_engine = _running_daemon_engine(state_dir)
        if args.restart:
            auto_restart_reason = "user passed --restart"
        elif args.no_auto_restart:
            auto_restart_reason = None
        elif running_engine is None:
            auto_restart_reason = (
                "running daemon predates the queue+mission merge "
                "(no live-progress wiring; no engine in status.json)"
            )
        elif running_engine != args.engine:
            auto_restart_reason = (
                f"running daemon uses engine={running_engine!r} but "
                f"this launcher requested engine={args.engine!r}"
            )

    if alive and auto_restart_reason and pid:
        sys.stdout.write(theme.dim(
            f"♻️  restarting daemon (pid={pid}): {auto_restart_reason}\n"
        ))
        sys.stdout.flush()
        if not _kill_daemon(pid, timeout=10.0):
            sys.stderr.write(
                f"⚠️  could not stop daemon pid={pid} cleanly. "
                f"You may need to kill it manually.\n"
            )
            return 1
        # Wait for status.json to clear / pid to disappear
        for _ in range(20):
            still_alive, _ = _is_daemon_alive(state_dir)
            if not still_alive:
                break
            time.sleep(0.2)
        alive = False
        pid = None

    if alive:
        sys.stdout.write(f"📌 attaching to running daemon (pid={pid}) at {state_dir}\n")
    elif args.attach_only:
        sys.stderr.write(f"--attach-only set, but no daemon is alive at {state_dir}\n")
        return 1
    else:
        backend, backend_bin = _detect_backend(args.backend)
        if backend == "memory":
            sys.stdout.write(theme.dim(
                "⚠️  no codex/copilot CLI found on PATH — falling back to "
                "deterministic memory backend (every /run returns a canned "
                "stub). Install codex or copilot, or pass --backend.\n"
            ))
        log_file = state_dir / "daemon.log"
        sys.stdout.write(theme.dim(
            f"🚀 starting daemon: engine={args.engine} backend={backend} "
            f"max_rounds={args.max_rounds} plan_mode={args.plan_mode} "
            f"max_follow_ups={args.max_follow_ups}\n"
            f"   state-dir: {state_dir}\n"
            f"   workdir:   {workdir}\n"
            f"   skills:    {skills_dir}\n"
            f"   log:       {log_file}\n"
        ))
        sys.stdout.flush()
        # Advance inbox offset to end-of-file so the freshly-spawned
        # daemon doesn't replay leftover commands (e.g. a `stop` the user
        # just sent to kill the *previous* daemon — without this, the new
        # daemon would consume that stale stop and shut itself down
        # immediately).
        try:
            inbox = state_dir / "inbox.jsonl"
            if inbox.is_file():
                size = inbox.stat().st_size
                (state_dir / "inbox.jsonl.offset").write_text(str(size), encoding="utf-8")
        except OSError:
            pass
        spawn_t = time.time()
        daemon_proc = _spawn_daemon(
            state_dir=state_dir,
            workdir=workdir,
            skills_dir=skills_dir,
            engine=args.engine,
            max_rounds=args.max_rounds,
            plan_mode=args.plan_mode,
            max_follow_ups=args.max_follow_ups,
            auto_follow_up=bool(args.auto_follow_up),
            enable_final_report=bool(getattr(args, "enable_final_report", False)),
            enable_pptx_report=bool(getattr(args, "enable_pptx_report", False)),
            checks=list(args.check or []),
            backend=backend,
            backend_bin=backend_bin,
            log_file=log_file,
        )
        if not _wait_for_daemon_up(
            state_dir,
            timeout=30,
            expect_pid=daemon_proc.pid,
            spawn_time=spawn_t,
        ):
            sys.stderr.write("❌ daemon failed to come up within 30s; tail of log:\n")
            try:
                lines = log_file.read_text(errors="replace").splitlines()[-40:]
                sys.stderr.write("\n".join(lines) + "\n")
            except OSError:
                pass
            _shutdown_daemon(daemon_proc, state_dir=state_dir, timeout=10)
            return 2

    from .chat_app import cmd_chat

    chat_args = argparse.Namespace(
        cmd="chat",
        state_dir=str(state_dir),
        verbose=True if getattr(args, "verbose", False) else False,
        no_plain_text_inject=False,
        from_start=False,
        color=getattr(args, "color", None),
        compact_banner=True,
    )

    rc = 0
    try:
        rc = cmd_chat(chat_args) or 0
    finally:
        if daemon_proc is not None:
            _shutdown_daemon(
                daemon_proc,
                state_dir=state_dir,
                timeout=args.shutdown_timeout,
            )
    return rc


__all__ = ["add_up_subcommand", "cmd_up"]
