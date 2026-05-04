"""``argus-skill daemon*`` subcommands: long-running operation.

Subcommands wired here:

  * ``daemon`` — start the daemon (foreground, blocks). Connects:
      - SkillLoop with the user's runner backend.
      - LocalBus control channel (always on, for CLI control).
      - Optional Telegram channel (when ``--telegram-bot-token`` /
        ``--telegram-chat-id`` are set).
      - Composite event sink: terminal + JSONL + optional Telegram.
  * ``daemon-status`` — read ``status.json`` and print a short report.
  * ``daemon-stop`` — write a ``stop`` command to the local bus.
  * ``daemon-inject`` — write an ``inject`` command to the local bus.
  * ``daemon-run`` — write a ``run`` command to the local bus.

These are flavour-of-cli wrappers around the JSONL bus (so they work
even without a Telegram bot configured).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from ..adapters.control_channels import (
    CompositeControlChannel,
    LocalBusControlChannel,
    TelegramControlChannel,
)
from ..adapters.event_sinks import (
    CompositeEventSink,
    JsonlEventSink,
    TelegramEventSink,
    TerminalEventSink,
)
from ..daemon.bus import (
    BusCommand,
    JsonlCommandBus,
    inspect_daemon_status,
)
from ..daemon.runtime import Daemon, DaemonConfig
from ..daemon.token_lock import acquire_token_lock
from ..loop import SkillLoop, SkillLoopConfig
from ..telegram.notifier import TelegramConfig, TelegramNotifier

# ---------------------------------------------------------------------------
# argparse plumbing
# ---------------------------------------------------------------------------

def add_daemon_subcommands(sub: argparse._SubParsersAction) -> None:
    daemon_p = sub.add_parser("daemon", help="run the argus-skill daemon (foreground)")
    daemon_p.add_argument("--skills-dir", default="skills")
    daemon_p.add_argument("--workdir", default=".")
    daemon_p.add_argument(
        "--state-dir",
        default=".argus-skill",
        help="where status.json, inbox.jsonl, outbox.jsonl live",
    )
    daemon_p.add_argument("--check", action="append", default=[])
    daemon_p.add_argument("--max-rounds", type=int, default=3)
    daemon_p.add_argument(
        "--scientist-model",
        default=os.environ.get("ARGUS_SKILL_SCIENTIST_MODEL", "gpt-5.4"),
    )
    daemon_p.add_argument(
        "--engineer-model",
        default=os.environ.get("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini"),
    )
    daemon_p.add_argument(
        "--reviewer-model",
        default=os.environ.get("ARGUS_SKILL_REVIEWER_MODEL"),
    )
    daemon_p.add_argument(
        "--telegram-bot-token",
        default=os.environ.get("TELEGRAM_BOT_TOKEN"),
    )
    daemon_p.add_argument(
        "--telegram-chat-id",
        default=os.environ.get("TELEGRAM_CHAT_ID"),
    )
    daemon_p.add_argument(
        "--no-telegram",
        action="store_true",
        help="disable Telegram even if env vars set",
    )
    daemon_p.add_argument(
        "--no-token-lock",
        action="store_true",
        help="skip token-lock check (debug only)",
    )
    daemon_p.add_argument(
        "--no-plain-text-inject",
        action="store_true",
        help=(
            "drop plain Telegram messages instead of treating them as /inject. "
            "Useful when you want the bot to ignore casual chat and only react "
            "to slash commands."
        ),
    )
    daemon_p.add_argument(
        "--mission-file",
        default=None,
        help=(
            "if set, run in mission mode: load mission.json (created by "
            "`argus-skill mission start`) and host an ArgusBot LoopEngine "
            "instead of the queue-based SkillLoop dispatcher. Enables "
            "true 7×24 unattended operation (planner-driven follow-ups, "
            "reviewer-gated done/continue/blocked, persistent operator "
            "criteria via /review /plan /mode)."
        ),
    )

    status_p = sub.add_parser("daemon-status", help="print the daemon's status.json")
    status_p.add_argument("--state-dir", default=".argus-skill")
    status_p.add_argument("--max-stale-seconds", type=int, default=15)

    stop_p = sub.add_parser("daemon-stop", help="ask the running daemon to shut down")
    stop_p.add_argument("--state-dir", default=".argus-skill")

    inject_p = sub.add_parser(
        "daemon-inject", help="inject text into the running daemon's next round"
    )
    inject_p.add_argument("text", help="guidance text to inject")
    inject_p.add_argument("--state-dir", default=".argus-skill")

    run_p = sub.add_parser("daemon-run", help="queue a task on the running daemon")
    run_p.add_argument("task", help="task description")
    run_p.add_argument("--state-dir", default=".argus-skill")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _state_paths(state_dir: str) -> dict:
    base = Path(state_dir)
    base.mkdir(parents=True, exist_ok=True)
    return {
        "status": str(base / "status.json"),
        "inbox": str(base / "inbox.jsonl"),
        "outbox": str(base / "outbox.jsonl"),
    }


def _make_bus_command(kind: str, text: str, *, source: str = "cli") -> BusCommand:
    return BusCommand(kind=kind, text=text, source=source, ts=time.time())


def cmd_daemon(args: argparse.Namespace, *, runner_factory) -> int:
    """``runner_factory()`` returns a ``RunnerBackend`` instance.

    Kept as a callable so the same plumbing can be reused with codex /
    claude / memory backends without binding the daemon to one.
    """
    paths = _state_paths(args.state_dir)
    runner = runner_factory()

    # Token lock — protects against two daemons fighting over one Telegram bot.
    lock_token = (args.telegram_bot_token or "local") if not args.no_token_lock else None
    lock_ctx = None
    if lock_token is not None:
        try:
            lock_ctx = acquire_token_lock(
                token=lock_token,
                owner_info={
                    "pid": os.getpid(),
                    "started_at": datetime.utcnow().isoformat(),
                    "host": "argus-skill",
                },
            )
        except RuntimeError as exc:
            sys.stderr.write(f"daemon: token lock busy: {exc}\n")
            return 2

    # ------------------------------------------------------------------
    # Branch: --mission-file → MissionDaemon (LoopEngine-backed),
    #         otherwise   → queue Daemon (existing behaviour, preserved).
    # ------------------------------------------------------------------
    mission_mode = bool(getattr(args, "mission_file", None))
    if mission_mode:
        return _run_mission_daemon(
            args=args,
            paths=paths,
            runner=runner,
            lock_ctx=lock_ctx,
        )

    config = SkillLoopConfig(
        scientist_model=args.scientist_model,
        engineer_model=args.engineer_model,
        reviewer_model=args.reviewer_model,
        max_rounds=args.max_rounds,
        check_commands=list(args.check or []),
    )
    loop = SkillLoop(
        skills_dir=Path(args.skills_dir),
        scientist_runner=runner,
        engineer_runner=runner,
        reviewer_runner=runner,
        config=config,
    )

    sinks = [TerminalEventSink(verbose=False), JsonlEventSink(paths["outbox"])]
    notifier = None
    telegram_active = (
        bool(args.telegram_bot_token and args.telegram_chat_id)
        and not args.no_telegram
    )
    if not telegram_active and not args.no_telegram:
        # Helpful warning: user almost certainly meant to enable Telegram
        # but forgot to source the secrets file.
        missing: list[str] = []
        if not args.telegram_bot_token:
            missing.append("--telegram-bot-token / TELEGRAM_BOT_TOKEN")
        if not args.telegram_chat_id:
            missing.append("--telegram-chat-id / TELEGRAM_CHAT_ID")
        if missing:
            sys.stderr.write(
                "argus-skill daemon: starting WITHOUT Telegram. Missing: "
                + ", ".join(missing)
                + ". (Pass --no-telegram to silence this warning.)\n"
            )
    if telegram_active:
        notifier = TelegramNotifier(TelegramConfig(
            bot_token=args.telegram_bot_token,
            chat_id=args.telegram_chat_id,
        ))
        sinks.append(TelegramEventSink(notifier=notifier))
    composite_sink = CompositeEventSink(sinks)

    # Route Telegram-poller errors to the outbox + stderr so an
    # operator can see them. We deliberately do NOT forward them back
    # over Telegram itself — that's the surface that's already failing.
    outbox_only_sink = JsonlEventSink(paths["outbox"])

    def _telegram_error(msg: str) -> None:
        try:
            outbox_only_sink.handle_event({"type": "telegram.error", "text": msg})
        except Exception:  # noqa: BLE001
            pass
        sys.stderr.write(f"[telegram.error] {msg}\n")
        sys.stderr.flush()

    daemon = Daemon(
        loop=loop,
        sinks=composite_sink,
        config=DaemonConfig(
            status_path=paths["status"],
            workdir=args.workdir,
        ),
    )

    channels = [LocalBusControlChannel(path=paths["inbox"], source="bus")]
    if telegram_active:
        channels.append(TelegramControlChannel(
            bot_token=args.telegram_bot_token,
            chat_id=args.telegram_chat_id,
            on_error=_telegram_error,
            plain_text_as_inject=not args.no_plain_text_inject,
        ))
    channel = CompositeControlChannel(channels)

    def _on_signal(_signum, _frame):
        daemon.stop()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        channel.start(daemon.handle_command)
        daemon.start()
        sys.stderr.write(
            f"argus-skill daemon: state_dir={args.state_dir} "
            f"telegram={'on' if telegram_active else 'off'}\n"
        )
        sys.stderr.flush()
        daemon.wait()
    finally:
        try:
            channel.stop()
        except Exception:  # noqa: BLE001
            pass
        if notifier is not None:
            try:
                notifier.close()
            except Exception:  # noqa: BLE001
                pass
        if lock_ctx is not None:
            try:
                lock_ctx.release()
            except Exception:  # noqa: BLE001
                pass
    return 0


def cmd_daemon_status(args: argparse.Namespace) -> int:
    paths = _state_paths(args.state_dir)
    inspection = inspect_daemon_status(
        paths["status"], stale_after_seconds=args.max_stale_seconds
    )
    output = {
        "alive": inspection.is_live,
        "reason": inspection.reason,
        "daemon_pid": inspection.daemon_pid,
        "updated_at": inspection.updated_at.isoformat() if inspection.updated_at else None,
        "payload": inspection.payload,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if inspection.is_live else 1


def cmd_daemon_stop(args: argparse.Namespace) -> int:
    paths = _state_paths(args.state_dir)
    bus = JsonlCommandBus(paths["inbox"])
    bus.publish(_make_bus_command("stop", ""))
    print(f"sent stop -> {paths['inbox']}")
    return 0


def cmd_daemon_inject(args: argparse.Namespace) -> int:
    paths = _state_paths(args.state_dir)
    bus = JsonlCommandBus(paths["inbox"])
    bus.publish(_make_bus_command("inject", args.text))
    print(f"sent inject ({len(args.text)} chars) -> {paths['inbox']}")
    return 0


def cmd_daemon_run(args: argparse.Namespace) -> int:
    paths = _state_paths(args.state_dir)
    bus = JsonlCommandBus(paths["inbox"])
    bus.publish(_make_bus_command("run", args.task))
    print(f"queued task -> {paths['inbox']}")
    return 0


# ---------------------------------------------------------------------------
# Mission-mode daemon (--mission-file)
# ---------------------------------------------------------------------------

def _run_mission_daemon(
    *,
    args: argparse.Namespace,
    paths: dict,
    runner,
    lock_ctx,
) -> int:
    """Mission-mode counterpart to ``cmd_daemon`` queue path.

    Loads ``mission.json``, builds a ``MissionDaemon`` (which hosts an
    ArgusBot ``LoopEngine``), and runs the same control-channel /
    sink wiring as the queue daemon.
    """
    from ..daemon.mission_runtime import (
        MissionConfig,
        MissionDaemon,
        MissionDaemonConfig,
    )

    mission_path = Path(args.mission_file).expanduser().resolve()
    if not mission_path.is_file():
        sys.stderr.write(f"daemon: --mission-file not found: {mission_path}\n")
        return 2
    try:
        mission = MissionConfig.from_json_file(mission_path)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"daemon: failed to load mission.json: {exc}\n")
        return 2

    # The argus-skill RunnerBackend (codex_backend) wraps an ArgusBot
    # CodexRunner internally; we need both the wrapper (for engineer +
    # matcher + distill) and the inner runner (for reviewer + planner +
    # report fallback).
    inner_argus_runner = getattr(runner, "argus_runner", None)
    if inner_argus_runner is None:
        sys.stderr.write(
            "daemon: --mission-file requires a runner that exposes "
            "`argus_runner` (ArgusBot CodexRunner). Memory/test backends "
            "are not supported in mission mode.\n"
        )
        return 2

    sinks = [TerminalEventSink(verbose=False), JsonlEventSink(paths["outbox"])]
    notifier = None
    telegram_active = (
        bool(args.telegram_bot_token and args.telegram_chat_id)
        and not args.no_telegram
    )
    if telegram_active:
        notifier = TelegramNotifier(TelegramConfig(
            bot_token=args.telegram_bot_token,
            chat_id=args.telegram_chat_id,
        ))
        sinks.append(TelegramEventSink(notifier=notifier))
    composite_sink = CompositeEventSink(sinks)

    outbox_only_sink = JsonlEventSink(paths["outbox"])

    def _telegram_error(msg: str) -> None:
        try:
            outbox_only_sink.handle_event({"type": "telegram.error", "text": msg})
        except Exception:  # noqa: BLE001
            pass
        sys.stderr.write(f"[telegram.error] {msg}\n")
        sys.stderr.flush()

    daemon = MissionDaemon(
        mission=mission,
        sinks=composite_sink,
        engineer_backend=runner,
        codex_runner=inner_argus_runner,
        config=MissionDaemonConfig(
            state_dir=args.state_dir,
            skills_dir=args.skills_dir,
        ),
    )

    channels = [LocalBusControlChannel(path=paths["inbox"], source="bus")]
    if telegram_active:
        channels.append(TelegramControlChannel(
            bot_token=args.telegram_bot_token,
            chat_id=args.telegram_chat_id,
            on_error=_telegram_error,
            plain_text_as_inject=not args.no_plain_text_inject,
        ))
    channel = CompositeControlChannel(channels)

    def _on_signal(_signum, _frame):
        daemon.stop()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        channel.start(daemon.handle_command)
        daemon.start()
        sys.stderr.write(
            f"argus-skill mission daemon: id={mission.mission_id} "
            f"plan_mode={mission.plan_mode} state_dir={args.state_dir} "
            f"telegram={'on' if telegram_active else 'off'}\n"
        )
        sys.stderr.flush()
        daemon.wait()
    finally:
        try:
            channel.stop()
        except Exception:  # noqa: BLE001
            pass
        if notifier is not None:
            try:
                notifier.close()
            except Exception:  # noqa: BLE001
                pass
        if lock_ctx is not None:
            try:
                lock_ctx.release()
            except Exception:  # noqa: BLE001
                pass
    return 0


__all__ = [
    "add_daemon_subcommands",
    "cmd_daemon",
    "cmd_daemon_status",
    "cmd_daemon_stop",
    "cmd_daemon_inject",
    "cmd_daemon_run",
]
