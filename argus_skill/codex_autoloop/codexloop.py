from __future__ import annotations

import argparse
import getpass
import json
import os
import select
import signal
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .apps.daemon_app import render_plan_context, render_review_context
from .banner import maybe_print_banner
from .copilot_proxy import bootstrap_proxy_checkout, managed_proxy_dir, resolve_proxy_dir
from .daemon_bus import BusCommand, JsonlCommandBus, read_status
from .model_catalog import MODEL_PRESETS
from .runner_backend import (
    DEFAULT_RUNNER_BACKEND,
    backend_supports_copilot_proxy,
    default_runner_bin,
    normalize_runner_backend,
)

DEFAULT_HOME_DIR = ".argusbot"
DEFAULT_TOKEN_LOCK_DIR = "/tmp/argusbot-token-locks"
DEFAULT_MAX_ROUNDS = 500
CHANNEL_TELEGRAM = "telegram"
CHANNEL_FEISHU = "feishu"


@dataclass
class TerminalCommand:
    kind: str
    text: str = ""


@dataclass(frozen=True)
class PlayMode:
    name: str
    planner_mode: str
    run_plan_mode: str
    note: str


PLAY_MODES: list[PlayMode] = [
    PlayMode(
        name="off",
        planner_mode="off",
        run_plan_mode="execute-only",
        note="Disable the plan agent.",
    ),
    PlayMode(
        name="auto",
        planner_mode="auto",
        run_plan_mode="execute-only",
        note="Enable planner updates, but do not auto-run follow-up unless user explicitly enables auto planning and confirms the session goal with /plan.",
    ),
    PlayMode(
        name="record",
        planner_mode="record",
        run_plan_mode="record-only",
        note="Planner records markdown only; no automatic follow-up execution.",
    ),
]


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.subcommand == "help":
        print(supported_features_text())
        return
    home_dir = Path(args.home_dir).resolve()
    home_dir.mkdir(parents=True, exist_ok=True)

    config_path = home_dir / "daemon_config.json"
    config = load_config(config_path)
    current_run_cwd = Path(args.run_cd).resolve() if args.run_cd else Path.cwd().resolve()
    if args.subcommand == "init":
        maybe_print_banner(subcommand=args.subcommand)
        stop_all_codexloop_loops(
            home_dir=home_dir,
            config=config,
            token_lock_dir=args.token_lock_dir,
        )
        config = run_interactive_config(home_dir=home_dir, run_cd=current_run_cwd)
        save_config(config_path, config)
        print(f"Saved config: {config_path}")
        pid = ensure_daemon_running(config=config, home_dir=home_dir, token_lock_dir=args.token_lock_dir)
        print(f"Daemon running in background. pid={pid}")
        print("Use `argusbot` to attach monitor and terminal control.")
        return
    if args.reconfigure or config is None or not is_config_usable(config):
        if args.reconfigure:
            stop_all_codexloop_loops(
                home_dir=home_dir,
                config=config,
                token_lock_dir=args.token_lock_dir,
            )
        config = run_interactive_config(home_dir=home_dir, run_cd=current_run_cwd)
        save_config(config_path, config)
        print(f"Saved config: {config_path}")

    if args.subcommand is None:
        ensure_daemon_running(config=config, home_dir=home_dir, token_lock_dir=args.token_lock_dir)
        run_monitor_console(
            config=config,
            home_dir=home_dir,
            token_lock_dir=args.token_lock_dir,
            tail_lines=max(1, int(args.tail_lines)),
        )
        return

    bus_dir = resolve_bus_dir(config, home_dir)
    if args.subcommand == "status":
        payload = read_status(bus_dir / "daemon_status.json")
        if payload is None:
            print("No daemon status found.")
            raise SystemExit(1)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.subcommand in {"run", "inject", "new", "mode", "plan", "review", "btw", "daemon-stop"}:
        ensure_daemon_running(config=config, home_dir=home_dir, token_lock_dir=args.token_lock_dir)

    if args.subcommand == "show-plan":
        payload = read_status(bus_dir / "daemon_status.json") or {}
        plan_path = payload.get("child_plan_report_path") or payload.get("child_plan_overview_path")
        if not isinstance(plan_path, str) or not plan_path.strip():
            raise SystemExit("No plan markdown available.")
        print(Path(plan_path).read_text(encoding="utf-8"))
        return
    if args.subcommand == "show-main-prompt":
        payload = read_status(bus_dir / "daemon_status.json") or {}
        prompt_path = payload.get("child_main_prompt_path")
        if not isinstance(prompt_path, str) or not prompt_path.strip():
            raise SystemExit("No main prompt markdown available.")
        print(Path(prompt_path).read_text(encoding="utf-8"))
        return
    if args.subcommand == "show-review":
        payload = read_status(bus_dir / "daemon_status.json") or {}
        review_dir = payload.get("child_review_summaries_dir")
        if not isinstance(review_dir, str) or not review_dir.strip():
            raise SystemExit("No review markdown available.")
        base = Path(review_dir)
        target = base / "index.md"
        if getattr(args, "text", ""):
            target = base / f"round-{int(args.text):03d}.md"
        print(target.read_text(encoding="utf-8"))
        return
    if args.subcommand == "show-plan-context":
        payload = read_status(bus_dir / "daemon_status.json") or {}
        print(
            render_plan_context(
                operator_messages_path=payload.get("child_operator_messages_path") or payload.get("operator_messages_file"),
                plan_overview_path=payload.get("child_plan_report_path") or payload.get("child_plan_overview_path"),
                plan_mode=str(payload.get("default_plan_mode") or payload.get("plan_mode") or "auto"),
            )
        )
        return
    if args.subcommand == "show-review-context":
        payload = read_status(bus_dir / "daemon_status.json") or {}
        print(
            render_review_context(
                operator_messages_path=payload.get("child_operator_messages_path") or payload.get("operator_messages_file"),
                review_summaries_dir=payload.get("child_review_summaries_dir"),
                state_file=payload.get("run_state_file"),
                check_commands=list(payload.get("run_check", [])) if isinstance(payload.get("run_check"), list) else [],
            )
        )
        return

    kind = "fresh-session" if args.subcommand == "new" else str(args.subcommand)
    raw_text = getattr(args, "text", "")
    if isinstance(raw_text, list):
        text = " ".join(raw_text).strip()
    else:
        text = str(raw_text).strip()
    publish_kind = "mode-menu" if args.subcommand == "mode" and not text else kind
    publish_command(bus_dir=bus_dir, kind=publish_kind, text=text, source="terminal")
    print(f"Sent: {args.subcommand}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="argusbot",
        description=(
            "Single-word entrypoint for the ArgusBot daemon. "
            "First run configures + starts daemon, later runs attach and monitor."
        ),
    )
    parser.add_argument(
        "--home-dir",
        default=DEFAULT_HOME_DIR,
        help="Daemon home directory (stores config/pid/logs/bus).",
    )
    parser.add_argument(
        "--run-cd",
        default=None,
        help="Run working directory for daemon-launched runs. Defaults to current shell directory.",
    )
    parser.add_argument(
        "--token-lock-dir",
        default=DEFAULT_TOKEN_LOCK_DIR,
        help="Global lock directory to enforce one daemon per Telegram token.",
    )
    parser.add_argument(
        "--tail-lines",
        type=int,
        default=20,
        help="Initial number of lines to show for each followed log file.",
    )
    parser.add_argument(
        "--reconfigure",
        action="store_true",
        help="Re-run interactive setup and overwrite daemon config.",
    )

    sub = parser.add_subparsers(dest="subcommand")
    sub.add_parser("help", help="Show supported ArgusBot features and commands.")
    sub.add_parser("init", help="Stop current workspace daemon, reconfigure, and restart fresh daemon.")
    sub.add_parser("status", help="Show daemon status JSON.")
    run = sub.add_parser("run", help="Start a run objective.")
    run.add_argument("text", nargs="+", help="Objective text.")
    new = sub.add_parser("new", help="Force the next run to start in a fresh main session.")
    new.add_argument("text", nargs="*", help=argparse.SUPPRESS)
    inject = sub.add_parser("inject", help="Inject instruction into active run.")
    inject.add_argument("text", nargs="+", help="Instruction text.")
    mode = sub.add_parser("mode", help="Hot-switch planner mode: off, auto, or record.")
    mode.add_argument("text", nargs="?", default="", help="Planner mode.")
    btw = sub.add_parser("btw", help="Ask the BTW side-agent a read-only project question.")
    btw.add_argument("text", nargs="+", help="Question text.")
    plan = sub.add_parser("plan", help="Send direction to the plan agent only.")
    plan.add_argument("text", nargs="+", help="Plan direction.")
    review = sub.add_parser("review", help="Send audit criteria to the reviewer only.")
    review.add_argument("text", nargs="+", help="Review criteria.")
    sub.add_parser("show-main-prompt", help="Print the latest main prompt markdown.")
    sub.add_parser("show-plan", help="Print the latest plan markdown.")
    sub.add_parser("show-plan-context", help="Ask daemon to print current plan directions and inputs.")
    show_review = sub.add_parser("show-review", help="Print reviewer summary markdown.")
    show_review.add_argument("text", nargs="?", default="", help="Optional round number.")
    sub.add_parser("show-review-context", help="Ask daemon to print current review direction, checks, and criteria.")
    sub.add_parser("stop", help="Stop active run.")
    sub.add_parser("daemon-stop", help="Stop daemon process.")
    return parser


def supported_features_text() -> str:
    return "\n".join(
        [
            "ArgusBot supported features",
            "",
            "Top-level commands:",
            "  argusbot",
            "      Attach monitor; auto-start daemon if needed.",
            "  argusbot help",
            "      Show this feature list.",
            "  argusbot init",
            "      Stop current workspace daemon, collect new config, and restart daemon in background.",
            "  argusbot status",
            "      Print daemon status JSON.",
            "  argusbot run <objective>",
            "      Start a new run objective.",
            "  argusbot new",
            "      Force the next run to start in a fresh main session.",
            "  argusbot inject <instruction>",
            "      Inject instruction into active run.",
            "  argusbot mode <off|auto|record>",
            "      Hot-switch daemon default planner mode.",
            "  argusbot btw <question>",
            "      Ask the read-only BTW side-agent a project question.",
            "  argusbot plan <direction>",
            "      Confirm the session-level goal for planning, or send direction to the plan agent. Required before auto follow-up.",
            "  argusbot review <criteria>",
            "      Send audit criteria to the reviewer only.",
            "  argusbot show-main-prompt",
            "      Print the latest main prompt markdown.",
            "  argusbot show-plan",
            "      Print the latest plan markdown.",
            "  argusbot show-plan-context",
            "      Print current plan directions and inputs.",
            "  argusbot show-review [round]",
            "      Print reviewer summary markdown.",
            "  argusbot show-review-context",
            "      Print current reviewer direction, checks, and criteria.",
            "  argusbot stop",
            "      Stop active run only.",
            "  argusbot daemon-stop",
            "      Stop daemon process.",
            "",
            "Attached monitor console commands:",
            "  /run /inject /mode /btw /plan /review /show-main-prompt /show-plan /show-plan-context /show-review [round] /show-review-context /status /stop /daemon-stop /help /new /exit",
            "  Plain text routes to /inject when running, else to /run.",
            "",
            "Planner Mode / 规划模式:",
            "  1) off",
            "  2) auto (default, planner on but no auto follow-up by default / 默认开启 planner 但不自动续跑)",
            "  3) record",
            "  Auto follow-up requires explicit /plan confirmation of the current session goal.",
            "",
            "Run working directory:",
            "  By default, uses the shell current working directory when config is created.",
        ]
    )


def load_config(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def save_config(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    path.chmod(0o600)


def run_interactive_config(*, home_dir: Path, run_cd: Path) -> dict[str, Any]:
    run_cd = run_cd.resolve()
    print("ArgusBot first-time setup")
    channel = prompt_control_channel(default=CHANNEL_TELEGRAM)
    runner_backend = prompt_runner_backend_choice()
    runner_bin = shutil.which(default_runner_bin(normalize_runner_backend(runner_backend))) or default_runner_bin(
        normalize_runner_backend(runner_backend)
    )
    if shutil.which(runner_bin) is None and not Path(runner_bin).expanduser().exists():
        raise SystemExit(f"{runner_backend} CLI not found in PATH. Install/configure it first.")
    token = None
    chat_id = None
    feishu_app_id = None
    feishu_app_secret = None
    feishu_chat_id = None
    if channel == CHANNEL_TELEGRAM:
        token = prompt_token()
        chat_id = prompt_chat_id()
    else:
        feishu_app_id = prompt_input("Feishu app id: ", default="").strip() or None
        feishu_app_secret = prompt_secret("Feishu app secret: ").strip() or None
        feishu_chat_id = prompt_input("Feishu chat id: ", default="").strip() or None
    check_cmd = prompt_input("Default check command (optional): ", default="").strip()
    model_preset = prompt_model_choice()
    if backend_supports_copilot_proxy(normalize_runner_backend(runner_backend)):
        use_copilot_proxy, copilot_proxy_dir, copilot_proxy_port = prompt_copilot_proxy_choice(
            preferred=(model_preset == "copilot")
        )
    else:
        use_copilot_proxy, copilot_proxy_dir, copilot_proxy_port = (False, None, 18080)
    play_mode = prompt_play_mode()
    objective_rewrite_enabled = prompt_objective_rewrite_choice()
    print(f"Run working directory: {run_cd}")
    return {
        "telegram_bot_token": token,
        "telegram_chat_id": chat_id,
        "feishu_app_id": feishu_app_id,
        "feishu_app_secret": feishu_app_secret,
        "feishu_chat_id": feishu_chat_id,
        "feishu_receive_id_type": "chat_id",
        "run_cd": str(run_cd),
        "run_check": (check_cmd if check_cmd else None),
        "run_max_rounds": DEFAULT_MAX_ROUNDS,
        "run_skip_git_repo_check": False,
        "run_full_auto": False,
        "run_yolo": True,
        "run_planner_mode": play_mode.planner_mode,
        "run_plan_mode": play_mode.run_plan_mode,
        "run_plan_request_delay_seconds": 600,
        "run_plan_auto_execute_delay_seconds": 600,
        "run_plan_record_file": None,
        "run_resume_last_session": True,
        "run_objective_rewrite": objective_rewrite_enabled,
        "run_runner_backend": runner_backend,
        "run_runner_bin": runner_bin,
        "run_main_reasoning_effort": None,
        "run_reviewer_reasoning_effort": None,
        "run_main_model": None,
        "run_reviewer_model": None,
        "run_model_preset": model_preset,
        "run_copilot_proxy": use_copilot_proxy,
        "run_copilot_proxy_dir": copilot_proxy_dir,
        "run_copilot_proxy_port": copilot_proxy_port,
        "bus_dir": str((home_dir / "bus").resolve()),
        "logs_dir": str((home_dir / "logs").resolve()),
    }


def is_config_usable(config: dict[str, Any]) -> bool:
    token = str(config.get("telegram_bot_token") or "").strip()
    feishu_app_id = str(config.get("feishu_app_id") or "").strip()
    feishu_app_secret = str(config.get("feishu_app_secret") or "").strip()
    feishu_chat_id = str(config.get("feishu_chat_id") or "").strip()
    run_cd = str(config.get("run_cd") or "").strip()
    return (looks_like_token(token) or bool(feishu_app_id and feishu_app_secret and feishu_chat_id)) and bool(run_cd)


def prompt_input(prompt: str, default: str) -> str:
    raw = input(prompt).strip()
    if not raw:
        return default
    return raw


def prompt_secret(prompt: str) -> str:
    return getpass.getpass(prompt).strip()


def prompt_yes_no(prompt: str, *, default: bool) -> bool:
    default_text = "Y/n" if default else "y/N"
    while True:
        raw = prompt_input(f"{prompt} [{default_text}]: ", default=("y" if default else "n")).strip().lower()
        if not raw:
            raw = "y" if default else "n"
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please answer y or n.", file=sys.stderr)


def prompt_control_channel(*, default: str = CHANNEL_TELEGRAM) -> str:
    options = [
        ("1", CHANNEL_TELEGRAM, "Telegram"),
        ("2", CHANNEL_FEISHU, "Feishu (适合CN网络环境)"),
    ]
    default_channel = default if default in {CHANNEL_TELEGRAM, CHANNEL_FEISHU} else CHANNEL_TELEGRAM
    default_index = next(index for index, channel, _ in options if channel == default_channel)
    print("Choose control channel:")
    for index, _, label in options:
        print(f"  {index}. {label}")
    while True:
        raw = prompt_input(f"Channel number [{default_index}]: ", default=default_index).strip()
        for index, channel, _ in options:
            if raw == index:
                return channel
        print("Invalid selection. Enter 1 or 2.", file=sys.stderr)


def prompt_token() -> str:
    while True:
        token = prompt_secret("Telegram bot token: ")
        if looks_like_token(token):
            return token
        print("Invalid token format. Expected <digits>:<secret>. Please try again.", file=sys.stderr)


def prompt_chat_id() -> str:
    while True:
        value = prompt_input("Telegram chat id (or 'auto'): ", default="auto").strip() or "auto"
        if value.lower() == "auto" or looks_like_chat_id(value):
            return value
        print("Invalid chat id. Use 'auto' or numeric id like 123456 or -100123456.", file=sys.stderr)


def prompt_model_choice() -> str | None:
    print("Choose model preset:")
    print("  0. inherit backend default (recommended)")
    for idx, preset in enumerate(MODEL_PRESETS, start=1):
        print(
            f"  {idx}. {preset.name}: "
            f"main={preset.main_model}/{preset.main_reasoning_effort}, "
            f"reviewer={preset.reviewer_model}/{preset.reviewer_reasoning_effort}"
        )
    while True:
        raw = prompt_input("Preset number: ", default="0").strip()
        try:
            index = int(raw)
        except ValueError:
            print("Invalid selection. Enter a number from the list.", file=sys.stderr)
            continue
        if index == 0:
            return None
        if 1 <= index <= len(MODEL_PRESETS):
            return MODEL_PRESETS[index - 1].name
        print("Selection out of range. Please choose one of the listed numbers.", file=sys.stderr)


def prompt_runner_backend_choice(default: str = DEFAULT_RUNNER_BACKEND) -> str:
    options = [
        ("1", "codex", "Codex CLI"),
        ("2", "claude", "Claude Code CLI"),
        ("3", "copilot", "GitHub Copilot CLI"),
    ]
    default_backend = normalize_runner_backend(default)
    default_index = next(index for index, backend, _ in options if backend == default_backend)
    print("Choose execution backend:")
    for index, _, label in options:
        print(f"  {index}. {label}")
    while True:
        raw = prompt_input("Backend number: ", default=default_index).strip()
        if not raw:
            raw = default_index
        for index, backend, _ in options:
            if raw == index:
                return backend
        print("Invalid selection. Enter 1, 2, or 3.", file=sys.stderr)


def prompt_copilot_proxy_choice(*, preferred: bool = False) -> tuple[bool, str | None, int]:
    detected = resolve_proxy_dir()
    if detected is not None:
        use_proxy = prompt_yes_no(
            f"Detected copilot-proxy at {detected}. Use it for Codex runs?",
            default=preferred,
        )
        if not use_proxy:
            return False, None, 18080
        return True, str(detected), 18080
    if not preferred:
        return False, None, 18080
    target_dir = managed_proxy_dir()
    install_proxy = prompt_yes_no(
        f"No local copilot-proxy checkout was found. Install it automatically into {target_dir}?",
        default=True,
    )
    if not install_proxy:
        return False, None, 18080
    try:
        resolved = bootstrap_proxy_checkout(
            on_progress=lambda message: print(f"[copilot-proxy] {message}", file=sys.stderr),
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return False, None, 18080
    print(f"[copilot-proxy] Ready at {resolved}", file=sys.stderr)
    return True, str(resolved), 18080


def prompt_play_mode() -> PlayMode:
    print("Choose Planner Mode:")
    for idx, mode in enumerate(PLAY_MODES, start=1):
        print(f"  {idx}. {mode.name}: {mode.note}")
    default_index = next((idx for idx, mode in enumerate(PLAY_MODES, start=1) if mode.name == "auto"), 1)
    while True:
        raw = prompt_input("Planner Mode number: ", default=str(default_index)).strip()
        try:
            index = int(raw)
        except ValueError:
            print("Invalid selection. Enter a number from the list.", file=sys.stderr)
            continue
        if 1 <= index <= len(PLAY_MODES):
            return PLAY_MODES[index - 1]
        print("Selection out of range. Please choose one of the listed numbers.", file=sys.stderr)


def prompt_objective_rewrite_choice() -> bool:
    print("Choose Objective Rewrite / 选择目标改写:")
    print("  1. off (Recommended): Keep /run text as-is. 默认关闭，不改写用户输入。")
    print(
        "  2. on: Rewrite a new idle /run request into an ArgusBot-style objective before sending it to the main agent. "
        "开启后会先整理成更适合 ArgusBot 的目标格式。"
    )
    print(
        "     Warning / 提示: this can help vague requests, but it may be a poor fit for specialized projects. "
        "对于特化项目效果不一定好，请按项目情况自行判断是否启用。"
    )
    while True:
        raw = prompt_input("Objective Rewrite number [1] / 输入改写模式编号 [1]: ", default="1").strip()
        if raw in {"", "1"}:
            return False
        if raw == "2":
            return True
        print("Invalid selection. Enter 1 or 2.", file=sys.stderr)


def looks_like_token(token: str) -> bool:
    if ":" not in token:
        return False
    left, right = token.split(":", 1)
    return left.isdigit() and bool(right.strip())


def looks_like_chat_id(value: str) -> bool:
    if not value:
        return False
    if value.startswith("-"):
        return value[1:].isdigit()
    return value.isdigit()


def resolve_daemon_launch_prefix() -> list[str]:
    daemon_bin = shutil.which("argusbot-daemon")
    if daemon_bin:
        return [daemon_bin]
    return [sys.executable, "-m", "codex_autoloop.telegram_daemon"]


def resolve_bus_dir(config: dict[str, Any], home_dir: Path) -> Path:
    raw = str(config.get("bus_dir") or (home_dir / "bus"))
    return Path(raw).expanduser().resolve()


def resolve_logs_dir(config: dict[str, Any], home_dir: Path) -> Path:
    raw = str(config.get("logs_dir") or (home_dir / "logs"))
    return Path(raw).expanduser().resolve()


def read_pid(pid_path: Path) -> int | None:
    if not pid_path.exists():
        return None
    try:
        raw = pid_path.read_text(encoding="utf-8").strip()
        pid = int(raw)
    except Exception:
        return None
    return pid if pid > 0 else None


def is_process_running(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def ensure_daemon_running(*, config: dict[str, Any], home_dir: Path, token_lock_dir: str) -> int:
    pid_path = home_dir / "daemon.pid"
    existing_pid = read_pid(pid_path)
    if is_process_running(existing_pid):
        assert existing_pid is not None
        return existing_pid

    bus_dir = resolve_bus_dir(config, home_dir)
    logs_dir = resolve_logs_dir(config, home_dir)
    bus_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    daemon_log = home_dir / "daemon.out"
    cmd = build_daemon_command(
        config=config,
        home_dir=home_dir,
        token_lock_dir=token_lock_dir,
    )
    with daemon_log.open("a", encoding="utf-8") as stream:
        proc = subprocess.Popen(
            cmd,
            stdout=stream,
            stderr=stream,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    time.sleep(1.0)
    if proc.poll() is not None:
        print("Daemon failed to start. Recent daemon.out:", file=sys.stderr)
        print(read_log_tail(daemon_log, max_lines=30), file=sys.stderr)
        raise SystemExit(2)
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    pid_path.chmod(0o600)
    print(f"Daemon started. pid={proc.pid}")
    return proc.pid


def stop_all_codexloop_loops(*, home_dir: Path, config: dict[str, Any] | None, token_lock_dir: str) -> None:
    _ = token_lock_dir
    stopped = stop_current_home_daemon(home_dir=home_dir, config=config)
    if stopped:
        print("Stopped 1 ArgusBot daemon process.")


def stop_current_home_daemon(*, home_dir: Path, config: dict[str, Any] | None) -> bool:
    pid_path = home_dir / "daemon.pid"
    pid = read_pid(pid_path)
    bus_dir = resolve_bus_dir(config, home_dir) if config else (home_dir / "bus").resolve()
    publish_daemon_stop_if_possible(bus_dir=bus_dir)
    if pid is None:
        pid_path.unlink(missing_ok=True)
        return False
    if wait_process_exit(pid, timeout_seconds=3.0):
        pid_path.unlink(missing_ok=True)
        return True
    terminated = terminate_process_tree(pid)
    pid_path.unlink(missing_ok=True)
    return terminated


def stop_global_daemons_from_token_locks(*, token_lock_dir: str) -> list[int]:
    lock_dir = Path(token_lock_dir).resolve()
    if not lock_dir.exists():
        return []
    stopped: list[int] = []
    for meta_path in lock_dir.glob("*.json"):
        payload = load_config(meta_path)
        if payload is None:
            continue
        pid_raw = payload.get("pid")
        bus_dir_raw = payload.get("bus_dir")
        if isinstance(bus_dir_raw, str) and bus_dir_raw.strip():
            publish_daemon_stop_if_possible(bus_dir=Path(bus_dir_raw).expanduser().resolve())
        pid = parse_pid(pid_raw)
        if pid is None:
            continue
        if wait_process_exit(pid, timeout_seconds=2.0):
            stopped.append(pid)
            meta_path.unlink(missing_ok=True)
            continue
        if terminate_process_tree(pid):
            stopped.append(pid)
            meta_path.unlink(missing_ok=True)
    return stopped


def parse_pid(value: Any) -> int | None:
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def publish_daemon_stop_if_possible(*, bus_dir: Path) -> None:
    try:
        publish_command(bus_dir=bus_dir, kind="daemon-stop", text="", source="terminal-init")
    except Exception:
        return


def wait_process_exit(pid: int, *, timeout_seconds: float) -> bool:
    if not is_process_running(pid):
        return True
    deadline = time.time() + max(0.0, timeout_seconds)
    while time.time() < deadline:
        if not is_process_running(pid):
            return True
        time.sleep(0.2)
    return not is_process_running(pid)


def terminate_process_tree(pid: int) -> bool:
    if not is_process_running(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True
    if wait_process_exit(pid, timeout_seconds=3.0):
        return True
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return True
    return wait_process_exit(pid, timeout_seconds=1.0)


def build_daemon_command(*, config: dict[str, Any], home_dir: Path, token_lock_dir: str) -> list[str]:
    token = str(config.get("telegram_bot_token") or "").strip()
    feishu_app_id = str(config.get("feishu_app_id") or "").strip()
    feishu_app_secret = str(config.get("feishu_app_secret") or "").strip()
    feishu_chat_id = str(config.get("feishu_chat_id") or "").strip()
    if not token and not (feishu_app_id and feishu_app_secret and feishu_chat_id):
        raise SystemExit("Missing control channel config in daemon config. Configure Telegram or Feishu.")
    run_cd = str(config.get("run_cd") or ".")
    chat_id = str(config.get("telegram_chat_id") or "auto")
    bus_dir = resolve_bus_dir(config, home_dir)
    logs_dir = resolve_logs_dir(config, home_dir)
    child_command = str(config.get("codex_autoloop_bin") or "").strip()
    cmd = [
        *resolve_daemon_launch_prefix(),
        "--run-cd",
        str(Path(run_cd).expanduser().resolve()),
        "--run-max-rounds",
        str(int(config.get("run_max_rounds", DEFAULT_MAX_ROUNDS))),
        "--bus-dir",
        str(bus_dir),
        "--logs-dir",
        str(logs_dir),
        "--run-state-file",
        str((home_dir / "last_state.json").resolve()),
        "--token-lock-dir",
        token_lock_dir,
        "--run-planner-mode",
        str(config.get("run_planner_mode") or "auto"),
        "--run-plan-mode",
        str(config.get("run_plan_mode") or "execute-only"),
        "--run-plan-request-delay-seconds",
        str(int(config.get("run_plan_request_delay_seconds", 600))),
        "--run-plan-auto-execute-delay-seconds",
        str(int(config.get("run_plan_auto_execute_delay_seconds", 600))),
        "--follow-up-auto-execute-seconds",
        str(int(config.get("follow_up_auto_execute_seconds", 600))),
    ]
    if token:
        cmd.extend(["--telegram-bot-token", token, "--telegram-chat-id", chat_id])
    if feishu_app_id and feishu_app_secret and feishu_chat_id:
        cmd.extend(
            [
                "--feishu-app-id",
                feishu_app_id,
                "--feishu-app-secret",
                feishu_app_secret,
                "--feishu-chat-id",
                feishu_chat_id,
                "--feishu-receive-id-type",
                str(config.get("feishu_receive_id_type") or "chat_id"),
            ]
        )
    if child_command:
        cmd.extend(["--argusbot-bin", child_command])
    run_check = config.get("run_check")
    if isinstance(run_check, str) and run_check.strip():
        cmd.extend(["--run-check", run_check.strip()])
    elif isinstance(run_check, list):
        for item in run_check:
            value = str(item).strip()
            if value:
                cmd.extend(["--run-check", value])
    run_model_preset = str(config.get("run_model_preset") or "").strip()
    if run_model_preset:
        cmd.extend(["--run-model-preset", run_model_preset])
    run_runner_backend = str(config.get("run_runner_backend") or DEFAULT_RUNNER_BACKEND).strip() or DEFAULT_RUNNER_BACKEND
    cmd.extend(["--run-runner-backend", run_runner_backend])
    run_runner_bin = str(config.get("run_runner_bin") or "").strip()
    if run_runner_bin:
        cmd.extend(["--run-runner-bin", run_runner_bin])
    if bool(config.get("run_copilot_proxy")):
        cmd.append("--run-copilot-proxy")
    else:
        cmd.append("--no-run-copilot-proxy")
    run_copilot_proxy_dir = str(config.get("run_copilot_proxy_dir") or "").strip()
    if run_copilot_proxy_dir:
        cmd.extend(["--run-copilot-proxy-dir", run_copilot_proxy_dir])
    run_copilot_proxy_port = int(config.get("run_copilot_proxy_port") or 18080)
    cmd.extend(["--run-copilot-proxy-port", str(run_copilot_proxy_port)])
    run_plan_record_file = str(config.get("run_plan_record_file") or "").strip()
    if run_plan_record_file:
        cmd.extend(["--run-plan-record-file", run_plan_record_file])
    run_main_model = str(config.get("run_main_model") or "").strip()
    if run_main_model:
        cmd.extend(["--run-main-model", run_main_model])
    run_main_effort = str(config.get("run_main_reasoning_effort") or "").strip()
    if run_main_effort:
        cmd.extend(["--run-main-reasoning-effort", run_main_effort])
    run_reviewer_model = str(config.get("run_reviewer_model") or "").strip()
    if run_reviewer_model:
        cmd.extend(["--run-reviewer-model", run_reviewer_model])
    run_reviewer_effort = str(config.get("run_reviewer_reasoning_effort") or "").strip()
    if run_reviewer_effort:
        cmd.extend(["--run-reviewer-reasoning-effort", run_reviewer_effort])
    if bool(config.get("run_skip_git_repo_check")):
        cmd.append("--run-skip-git-repo-check")
    if bool(config.get("run_full_auto")):
        cmd.append("--run-full-auto")
    cmd.append("--run-yolo")
    if bool(config.get("run_resume_last_session", True)):
        cmd.append("--run-resume-last-session")
    else:
        cmd.append("--no-run-resume-last-session")
    if bool(config.get("run_objective_rewrite", False)):
        cmd.append("--run-objective-rewrite")
    else:
        cmd.append("--no-run-objective-rewrite")
    return cmd


def read_log_tail(path: Path, *, max_lines: int) -> str:
    if not path.exists():
        return "<log file not found>"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return "<unable to read log>"
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


def publish_command(*, bus_dir: Path, kind: str, text: str, source: str) -> None:
    bus_dir.mkdir(parents=True, exist_ok=True)
    bus = JsonlCommandBus(bus_dir / "daemon_commands.jsonl")
    bus.publish(BusCommand(kind=kind, text=text, source=source, ts=time.time()))


def build_monitor_session_plan_hint(status_payload: dict[str, Any] | None) -> str | None:
    if not isinstance(status_payload, dict):
        return None
    planner_mode = str(status_payload.get("default_plan_mode") or status_payload.get("plan_mode") or "").strip().lower()
    if planner_mode != "auto":
        return None
    confirmed = bool(status_payload.get("session_plan_goal_confirmed"))
    pending_goal = str(status_payload.get("pending_session_plan_goal") or "").strip()
    active_goal = str(status_payload.get("active_session_plan_goal") or "").strip()
    if confirmed or pending_goal or active_goal:
        return None
    return (
        "[monitor] auto mode is waiting for /plan\n"
        "[CN] 请先发送 `/plan <本 session 总目标>`，确认这整个 session 要完成什么；确认前，其他任务消息会先提醒你补这一步。\n"
        "[EN] Send `/plan <session goal>` first to confirm the overall goal for this whole session; until then, task messages will be intercepted by that reminder."
    )


def run_monitor_console(
    *,
    config: dict[str, Any],
    home_dir: Path,
    token_lock_dir: str,
    tail_lines: int,
) -> None:
    bus_dir = resolve_bus_dir(config, home_dir)
    logs_dir = resolve_logs_dir(config, home_dir)
    daemon_out = home_dir / "daemon.out"
    events_log = logs_dir / "daemon-events.jsonl"
    status_path = bus_dir / "daemon_status.json"

    print("Attached to ArgusBot daemon.")
    print(
        "Commands: /run /inject /mode /btw /plan /review /show-main-prompt /show-plan /show-plan-context /show-review [round] /show-review-context /status /stop /daemon-stop /help /new /exit"
    )
    print("Plain text: running -> inject, idle -> run")
    initial_status = read_status(status_path) or {}
    hint = build_monitor_session_plan_hint(initial_status)
    if hint:
        print(hint)
    print("")

    tracked_offsets: dict[Path, int] = {}
    file_labels: dict[Path, str] = {}

    def ensure_tracked(path: Path, label: str) -> None:
        if path in tracked_offsets:
            return
        if path.exists():
            for line in tail_file(path, max_lines=tail_lines):
                print(f"[{label}] {line}")
            tracked_offsets[path] = path.stat().st_size
        else:
            tracked_offsets[path] = 0
        file_labels[path] = label

    ensure_tracked(daemon_out, "daemon")
    ensure_tracked(events_log, "events")

    last_child_log: Path | None = None
    while True:
        status_payload = read_status(status_path) or {}
        child_log_raw = status_payload.get("child_log_path")
        child_log_path = Path(child_log_raw).resolve() if isinstance(child_log_raw, str) and child_log_raw else None
        if child_log_path is not None and child_log_path != last_child_log:
            print(f"[monitor] child log switched to: {child_log_path}")
            ensure_tracked(child_log_path, "child")
            last_child_log = child_log_path

        for path, offset in list(tracked_offsets.items()):
            lines, next_offset = read_new_lines(path, offset)
            tracked_offsets[path] = next_offset
            label = file_labels.get(path, "log")
            for line in lines:
                print(f"[{label}] {line}")

        line = read_input_line(timeout_seconds=0.5)
        if line is None:
            continue
        if line == "__EOF__":
            print("Input closed. Exiting monitor.")
            return

        running = bool((read_status(status_path) or {}).get("running"))
        parsed = parse_terminal_command(line, running=running)
        if parsed is None:
            continue
        if parsed.kind == "exit":
            print("Leaving monitor.")
            return
        if parsed.kind == "help":
            print(
                "Commands: /run /inject /mode /btw /plan /review /show-main-prompt /show-plan /show-plan-context /show-review [round] /show-review-context /status /stop /daemon-stop /help /new /exit\n"
                "Plain text routes to inject when running, else run."
            )
            hint = build_monitor_session_plan_hint(read_status(status_path) or {})
            if hint:
                print(hint)
            continue
        if parsed.kind == "status":
            payload = read_status(status_path)
            if payload is None:
                print("No daemon status found.")
            else:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            continue
        if parsed.kind == "show-main-prompt":
            payload = read_status(status_path) or {}
            prompt_path = payload.get("child_main_prompt_path")
            if isinstance(prompt_path, str) and prompt_path.strip():
                print(Path(prompt_path).read_text(encoding="utf-8"))
            else:
                print("No main prompt markdown available.")
            continue
        if parsed.kind == "show-plan":
            payload = read_status(status_path) or {}
            plan_path = payload.get("child_plan_report_path") or payload.get("child_plan_overview_path")
            if isinstance(plan_path, str) and plan_path.strip():
                print(Path(plan_path).read_text(encoding="utf-8"))
            else:
                print("No plan markdown available.")
            continue
        if parsed.kind == "show-review":
            payload = read_status(status_path) or {}
            review_dir = payload.get("child_review_summaries_dir")
            if isinstance(review_dir, str) and review_dir.strip():
                base = Path(review_dir)
                target = base / "index.md"
                if parsed.text:
                    target = base / f"round-{int(parsed.text):03d}.md"
                print(target.read_text(encoding="utf-8"))
            else:
                print("No review markdown available.")
            continue
        if parsed.kind == "show-plan-context":
            payload = read_status(status_path) or {}
            print(
                render_plan_context(
                    operator_messages_path=payload.get("child_operator_messages_path") or payload.get("operator_messages_file"),
                    plan_overview_path=payload.get("child_plan_report_path") or payload.get("child_plan_overview_path"),
                    plan_mode=str(payload.get("default_plan_mode") or payload.get("plan_mode") or "auto"),
                )
            )
            continue
        if parsed.kind == "show-review-context":
            payload = read_status(status_path) or {}
            print(
                render_review_context(
                    operator_messages_path=payload.get("child_operator_messages_path") or payload.get("operator_messages_file"),
                    review_summaries_dir=payload.get("child_review_summaries_dir"),
                    state_file=payload.get("run_state_file"),
                    check_commands=list(payload.get("run_check", [])) if isinstance(payload.get("run_check"), list) else [],
                )
            )
            continue
        if parsed.kind in {"run", "inject"}:
            ensure_daemon_running(config=config, home_dir=home_dir, token_lock_dir=token_lock_dir)
        publish_command(bus_dir=bus_dir, kind=parsed.kind, text=parsed.text, source="terminal-console")
        print(f"Sent: {parsed.kind}")


def tail_file(path: Path, *, max_lines: int) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    if len(lines) <= max_lines:
        return lines
    return lines[-max_lines:]


def read_new_lines(path: Path, offset: int) -> tuple[list[str], int]:
    if not path.exists():
        return [], offset
    size = path.stat().st_size
    if size < offset:
        offset = 0
    if size == offset:
        return [], offset
    try:
        with path.open("r", encoding="utf-8") as stream:
            stream.seek(offset)
            chunk = stream.read()
            next_offset = stream.tell()
    except Exception:
        return [], offset
    if not chunk:
        return [], next_offset
    return chunk.splitlines(), next_offset


def read_input_line(*, timeout_seconds: float) -> str | None:
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
    except Exception:
        time.sleep(timeout_seconds)
        return None
    if not ready:
        return None
    line = sys.stdin.readline()
    if line == "":
        return "__EOF__"
    return line.rstrip("\n")


def parse_terminal_command(raw: str, *, running: bool) -> TerminalCommand | None:
    text = raw.strip()
    if not text:
        return None
    if text in {"/exit", "/quit", "exit", "quit"}:
        return TerminalCommand(kind="exit")
    if text in {"/help", "help"}:
        return TerminalCommand(kind="help")
    if text in {"/status", "status"}:
        return TerminalCommand(kind="status")
    if text in {"/stop", "stop"}:
        return TerminalCommand(kind="stop")
    if text in {"/new", "new", "/fresh", "fresh", "/fresh-session", "fresh-session", "/new-session", "new-session"}:
        return TerminalCommand(kind="fresh-session")
    if text in {"/daemon-stop", "daemon-stop", "/disable", "disable"}:
        return TerminalCommand(kind="daemon-stop")
    if text in {"/mode", "mode"}:
        return TerminalCommand(kind="mode-menu", text="")
    if text.startswith("/mode "):
        return TerminalCommand(kind="mode", text=text[len("/mode ") :].strip())
    if text.startswith("/btw "):
        return TerminalCommand(kind="btw", text=text[len("/btw ") :].strip())
    if text.startswith("/plan "):
        return TerminalCommand(kind="plan", text=text[len("/plan ") :].strip())
    if text.startswith("/review "):
        return TerminalCommand(kind="review", text=text[len("/review ") :].strip())
    if text in {"/show-main-prompt", "show-main-prompt"}:
        return TerminalCommand(kind="show-main-prompt")
    if text in {"/show-plan", "show-plan"}:
        return TerminalCommand(kind="show-plan")
    if text in {"/show-plan-context", "show-plan-context"}:
        return TerminalCommand(kind="show-plan-context")
    if text.startswith("/show-review "):
        return TerminalCommand(kind="show-review", text=text[len("/show-review ") :].strip())
    if text in {"/show-review", "show-review"}:
        return TerminalCommand(kind="show-review")
    if text in {"/show-review-context", "show-review-context"}:
        return TerminalCommand(kind="show-review-context")
    if text.startswith("/run "):
        objective = text[len("/run ") :].strip()
        if not objective:
            return None
        return TerminalCommand(kind="run", text=objective)
    if text == "/run":
        return None
    if text.startswith("/inject "):
        instruction = text[len("/inject ") :].strip()
        if not instruction:
            return None
        return TerminalCommand(kind="inject", text=instruction)
    if text == "/inject":
        return None
    if text.startswith("/"):
        return None
    return TerminalCommand(kind="inject" if running else "run", text=text)


if __name__ == "__main__":
    main()
