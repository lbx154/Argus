"""``argus-skill go`` — one-shot mission daemon + chat REPL in one command.

The vanilla flow is two terminals: ``argus-skill mission start`` →
``argus-skill daemon --mission-file ...`` (terminal A) →
``argus-skill chat --state-dir ...`` (terminal B). That's three commands
across two windows. ``go`` collapses it to one:

  $ argus-skill go
  🎯 objective> Build a Python word-freq CLI in /tmp/...
  🚀 daemon up (pid=12345)
  > /status
  ...
  > /exit
  🛑 stopping daemon... done.

Behaviour:

  * If a daemon is already running at ``--state-dir``, ``go`` skips
    mission creation/spawn and just opens the chat REPL against it.
  * Otherwise it prompts for an objective (or accepts it as the first
    positional arg), creates ``mission.json``, spawns the daemon as a
    subprocess (logs to ``state-dir/missions/<id>/daemon.log``), then
    runs the chat REPL inline.
  * On REPL exit, sends ``/stop`` and waits up to 90s for graceful
    shutdown. Second Ctrl-C escalates to SIGTERM/SIGKILL.

Defaults are tuned for "I just want to chat with my agent":

  * ``--state-dir`` = ``~/.argus-skill/mission-state``
  * ``--plan-mode`` = ``auto``  (unattended chaining)
  * ``--max-rounds`` = ``20``
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from ..daemon.bus import BusCommand, JsonlCommandBus, inspect_daemon_status

DEFAULT_STATE_DIR = "~/.argus-skill/mission-state"
DEFAULT_PLAN_MODE = "auto"
DEFAULT_MAX_ROUNDS = 20
DEFAULT_SKILLS_DIR = "/home/argustest/argus-skill/skills"
NODE_BIN_PATH = "/home/argustest/.nvm/versions/node/v22.22.0/bin"


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def add_go_subcommand(sub: argparse._SubParsersAction) -> None:
    go_p = sub.add_parser(
        "go",
        help=(
            "one-shot: create a mission, spawn a daemon, open the chat REPL "
            "(this is the easy-mode entrypoint)"
        ),
    )
    go_p.add_argument(
        "objective",
        nargs="?",
        default=None,
        help="mission objective text (omit to be prompted)",
    )
    go_p.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    go_p.add_argument("--skills-dir", default=DEFAULT_SKILLS_DIR)
    go_p.add_argument(
        "--plan-mode",
        choices=("auto", "off", "record"),
        default=DEFAULT_PLAN_MODE,
    )
    go_p.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS)
    go_p.add_argument(
        "--check",
        action="append",
        default=[],
        help="acceptance check command (repeatable)",
    )
    go_p.add_argument("--workdir", default=None,
                      help="defaults to cwd; passed into mission.json")
    go_p.add_argument(
        "--attach-only",
        action="store_true",
        help=(
            "skip mission creation; just open chat REPL against an existing "
            "daemon at --state-dir (errors out if none is running)"
        ),
    )
    go_p.add_argument(
        "--shutdown-timeout",
        type=int,
        default=90,
        help="seconds to wait for graceful daemon shutdown after REPL exits",
    )
    go_p.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "start chat REPL in quiet mode (only user-facing events). "
            "Default for `argus-skill go` is verbose-on so you see the "
            "engineer/reviewer/planner stream as it happens."
        ),
    )
    go_p.add_argument("--color", dest="color", action="store_true", default=None,
                      help="force ANSI colors on (auto-detect by default)")
    go_p.add_argument("--no-color", dest="color", action="store_false",
                      help="disable ANSI colors (auto-detect by default)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state_paths(state_dir: Path) -> dict:
    return {
        "status": state_dir / "status.json",
        "inbox": state_dir / "inbox.jsonl",
        "outbox": state_dir / "outbox.jsonl",
    }


def _is_daemon_alive(state_dir: Path) -> tuple[bool, int | None]:
    status = state_dir / "status.json"
    if not status.is_file():
        return False, None
    inspection = inspect_daemon_status(str(status), stale_after_seconds=15)
    return inspection.is_live, inspection.daemon_pid


_ANSI_RE = __import__("re").compile(r"\x1b\[[0-9;]*[a-zA-Z~]")


def _drain_pasted_lines(timeout: float = 0.10, *, max_bytes: int = 16384) -> list[str]:
    """Read any extra lines that are *already* sitting in stdin's buffer.

    When a user pastes multi-line text into the ``input()`` prompt, the kernel's
    line discipline only delivers the first line to ``input()``; the rest stays
    queued.  After ``input()`` returns we use ``select`` with a short timeout to
    detect that burst and drain it.  A regular interactive single-line typer
    sees no queued data and we return immediately.

    ANSI escape sequences (e.g. bracketed-paste markers ``\\x1b[200~`` /
    ``\\x1b[201~`` that some terminals inject) are stripped from the result.
    """
    import os
    import select

    try:
        fd = sys.stdin.fileno()
    except (AttributeError, OSError):
        return []

    chunks: list[bytes] = []
    total = 0
    poll = timeout
    while total < max_bytes:
        try:
            ready, _, _ = select.select([fd], [], [], poll)
        except (ValueError, OSError):
            break
        if not ready:
            break
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        # After the first follow-on chunk arrives, give a much shorter
        # grace period for the rest of the same paste burst.
        poll = 0.04

    if not chunks:
        return []
    text = b"".join(chunks)[:max_bytes].decode("utf-8", errors="replace")
    text = _ANSI_RE.sub("", text)
    return [line.strip() for line in text.splitlines() if line.strip()]


def _prompt_objective() -> str:
    sys.stdout.write(
        "🎯 mission objective (single line or paste multi-line; "
        "Ctrl-C to abort):\n> "
    )
    sys.stdout.flush()
    try:
        first = input()
    except (EOFError, KeyboardInterrupt):
        sys.stdout.write("\n")
        return ""
    parts = [first.strip()]
    parts.extend(p.strip() for p in _drain_pasted_lines())
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) > 1:
        sys.stdout.write(f"📝 collected {len(parts)} pasted lines into one objective\n")
        sys.stdout.flush()
    return " ".join(parts)


def _create_mission(
    *,
    state_dir: Path,
    objective: str,
    workdir: str | None,
    plan_mode: str,
    max_rounds: int,
    checks: list[str],
) -> Path:
    """Reuse mission_app.cmd_mission_start by synthesising its args.

    Suppresses the verbose mission-id / mission.json / "Next:" output —
    `argus-skill go` shows the same information inside the chat banner.
    """
    from .mission_app import cmd_mission_start
    import contextlib
    import io

    ns = argparse.Namespace(
        cmd="mission",
        mission_cmd="start",
        objective=objective,
        state_dir=str(state_dir),
        workdir=workdir,
        check=list(checks),
        max_rounds=max_rounds,
        plan_mode=plan_mode,
        main_model=os.environ.get("ARGUS_SKILL_MAIN_MODEL", "gpt-5.4-mini"),
        reviewer_model=os.environ.get("ARGUS_SKILL_REVIEWER_MODEL", "gpt-5.4-mini"),
        plan_model=os.environ.get("ARGUS_SKILL_PLAN_MODEL", "gpt-5.4"),
        main_reasoning_effort="medium",
        reviewer_reasoning_effort="medium",
        plan_reasoning_effort="high",
        quiet=True,
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cmd_mission_start(ns)
    if rc != 0:
        raise RuntimeError(f"mission start failed (rc={rc})\n{buf.getvalue()}")
    active = json.loads((state_dir / "missions" / "active.json").read_text())
    return state_dir / "missions" / active["mission_id"] / "mission.json"


def _spawn_daemon(
    *,
    state_dir: Path,
    mission_file: Path,
    skills_dir: str,
    log_file: Path,
) -> subprocess.Popen:
    """Launch the daemon as a child process; pipe its stdio to log_file."""
    env = os.environ.copy()
    env.setdefault("ARGUS_SKILL_BACKEND", "codex")
    # Make sure the codex CLI binary is reachable from the daemon.
    if NODE_BIN_PATH not in env.get("PATH", ""):
        env["PATH"] = f"{NODE_BIN_PATH}:{env.get('PATH', '')}"

    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log_file, "ab", buffering=0)

    cmd = [
        sys.executable,
        "-m",
        "argus_skill",
        "daemon",
        "--mission-file",
        str(mission_file),
        "--state-dir",
        str(state_dir),
        "--skills-dir",
        skills_dir,
        "--no-token-lock",
        "--no-telegram",
    ]

    # start_new_session=True puts the daemon in its own process group so
    # that Ctrl-C in the REPL hits the parent only; we forward /stop
    # explicitly when we want it.
    return subprocess.Popen(
        cmd,
        stdout=fh,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
        cwd=str(Path.cwd()),
    )


def _wait_for_daemon_up(
    state_dir: Path,
    *,
    timeout: float = 8.0,
    expected_mission_id: str | None = None,
) -> bool:
    """Wait until the daemon has written status.json.

    If ``expected_mission_id`` is provided, also wait until status.json
    reflects that specific mission (not a stale value from a previous
    daemon at the same state-dir). This prevents the chat REPL from
    showing yesterday's mission in its opening banner.
    """
    deadline = time.monotonic() + timeout
    status = state_dir / "status.json"
    while time.monotonic() < deadline:
        if status.is_file():
            if expected_mission_id is None:
                return True
            try:
                payload = json.loads(status.read_text())
                if payload.get("mission_id") == expected_mission_id:
                    return True
            except (json.JSONDecodeError, OSError):
                pass  # mid-write or transient — keep polling
        time.sleep(0.2)
    return False


def _send_stop(state_dir: Path) -> None:
    paths = _state_paths(state_dir)
    try:
        bus = JsonlCommandBus(str(paths["inbox"]))
        bus.publish(BusCommand(kind="stop", text="", source="go", ts=time.time()))
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"(failed to publish /stop: {exc})\n")


def _shutdown_daemon(
    proc: subprocess.Popen,
    *,
    state_dir: Path,
    timeout: int,
) -> None:
    """Try /stop first (graceful), then SIGTERM, then SIGKILL."""
    if proc.poll() is not None:
        return
    sys.stdout.write("🛑 stopping daemon (waiting for graceful shutdown — ")
    sys.stdout.write("LoopEngine generates a final report after success)...\n")
    sys.stdout.flush()
    _send_stop(state_dir)
    deadline = time.monotonic() + timeout
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.5)
    if proc.poll() is not None:
        sys.stdout.write("✅ daemon exited cleanly\n")
        return
    sys.stdout.write(f"⚠️  daemon still alive after {timeout}s — sending SIGTERM\n")
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.2)
    if proc.poll() is None:
        sys.stdout.write("⚠️  SIGKILL\n")
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=3)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def cmd_go(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)

    alive, pid = _is_daemon_alive(state_dir)

    daemon_proc: subprocess.Popen | None = None
    if alive:
        sys.stdout.write(f"📌 attaching to running daemon (pid={pid})\n")
    elif args.attach_only:
        sys.stderr.write(
            f"--attach-only set, but no daemon is alive at {state_dir}\n"
        )
        return 1
    else:
        # Need to spawn a fresh daemon → first ensure we have an objective.
        objective = args.objective
        if not objective:
            objective = _prompt_objective()
            if not objective:
                sys.stderr.write("aborted: no objective\n")
                return 1

        try:
            mission_file = _create_mission(
                state_dir=state_dir,
                objective=objective,
                workdir=args.workdir,
                plan_mode=args.plan_mode,
                max_rounds=args.max_rounds,
                checks=args.check or [],
            )
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"❌ mission create failed: {exc}\n")
            return 2

        # mission_file lives at state_dir/missions/<id>/mission.json — the
        # parent directory name is exactly the mission_id we just created.
        new_mission_id = mission_file.parent.name

        from ..cli.theme import Theme as _Theme
        _t = _Theme.auto(force=getattr(args, "color", None))
        log_file = mission_file.parent / "daemon.log"
        sys.stdout.write(_t.dim(
            f"✅ mission {new_mission_id}\n"
            f"   spawning daemon (log: {log_file}) …\n"
        ))
        daemon_proc = _spawn_daemon(
            state_dir=state_dir,
            mission_file=mission_file,
            skills_dir=args.skills_dir,
            log_file=log_file,
        )
        if not _wait_for_daemon_up(
            state_dir, timeout=10, expected_mission_id=new_mission_id
        ):
            sys.stderr.write(
                "❌ daemon failed to publish fresh status within 10s "
                f"(expected mission_id={new_mission_id}); tail of log:\n"
            )
            try:
                lines = log_file.read_text(errors="replace").splitlines()[-40:]
                sys.stderr.write("\n".join(lines) + "\n")
            except OSError:
                pass
            _shutdown_daemon(daemon_proc, state_dir=state_dir, timeout=10)
            return 2
        # Branded banner is now rendered by chat_app on entry; we don't
        # print our own welcome here so the user only sees one banner.

    # --- Open chat REPL inline ------------------------------------------------
    from .chat_app import cmd_chat

    chat_args = argparse.Namespace(
        cmd="chat",
        state_dir=str(state_dir),
        # `argus-skill go` defaults to verbose-on (show the engineer/reviewer
        # /planner stream as it happens). --quiet flips it off.
        verbose=False if getattr(args, "quiet", False) else True,
        no_plain_text_inject=False,
        from_start=False,
        color=getattr(args, "color", None),
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


__all__ = ["add_go_subcommand", "cmd_go"]
