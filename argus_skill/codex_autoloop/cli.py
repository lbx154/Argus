from __future__ import annotations

import argparse
import json
from pathlib import Path

from .copilot_proxy import AUTO_DETECTED_PROXY_DIR_HELP
from .apps.cli_app import run_cli
from .runner_backend import DEFAULT_RUNNER_BACKEND, RUNNER_BACKEND_CHOICES
from .apps.shell_utils import (
    control_help_text,
    format_control_status,
    looks_like_bot_token,
    parse_telegram_events,
    resolve_final_report_file,
    resolve_operator_messages_file,
    resolve_plan_overview_file,
    resolve_pptx_report_file,
    resolve_review_summaries_dir,
)

__all__ = [
    "build_parser",
    "control_help_text",
    "format_control_status",
    "looks_like_bot_token",
    "parse_telegram_events",
    "resolve_final_report_file",
    "resolve_operator_messages_file",
    "resolve_plan_report_file",
    "resolve_plan_todo_file",
    "resolve_pptx_report_file",
    "resolve_review_summaries_dir",
]


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    objective = " ".join(args.objective).strip()
    if not objective:
        parser.error("objective cannot be empty")
    if args.stall_soft_idle_seconds < 0:
        parser.error("--stall-soft-idle-seconds must be >= 0")
    if args.stall_hard_idle_seconds < 0:
        parser.error("--stall-hard-idle-seconds must be >= 0")
    if (
        args.stall_soft_idle_seconds > 0
        and args.stall_hard_idle_seconds > 0
        and args.stall_hard_idle_seconds < args.stall_soft_idle_seconds
    ):
        parser.error("--stall-hard-idle-seconds must be >= --stall-soft-idle-seconds")
    if args.plan_update_interval_seconds < 0:
        parser.error("--plan-update-interval-seconds must be >= 0")

    if not args.planner:
        args.plan_mode = "off"

    if args.main_prompt_file is None:
        args.main_prompt_file = resolve_main_prompt_file(state_file=args.state_file, control_file=args.control_file)

    # Interactive PPTX report prompt: when --pptx-report is not explicitly set
    # and stdin is a terminal, ask the user before starting.
    if args.pptx_report is None and _should_prompt_pptx():
        args.pptx_report = _ask_pptx_report()
    # --no-pptx-report explicitly disables PPTX generation.
    if args.pptx_report is False:
        args.pptx_report_file = None

    try:
        payload, exit_code = run_cli(args)
    except ValueError as exc:
        parser.error(str(exc))
        return

    if args.plan_todo_file:
        _mirror_plan_report_to_todo(
            report_path=payload.get("plan_overview_file"),
            todo_path=args.plan_todo_file,
        )

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="argusbot-run",
        description=(
            "Run ArgusBot in an automatic supervisor loop. "
            "A reviewer sub-agent gates completion and can force additional rounds."
        ),
    )
    parser.add_argument("objective", nargs="+", help="Task objective passed to the primary agent.")
    parser.add_argument(
        "--runner-backend",
        default=DEFAULT_RUNNER_BACKEND,
        choices=RUNNER_BACKEND_CHOICES,
        help="Execution backend used for agent runs.",
    )
    parser.add_argument(
        "--runner-bin",
        dest="runner_bin",
        default=None,
        help="CLI binary path for the selected execution backend.",
    )
    parser.add_argument(
        "--copilot-proxy",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Route Codex CLI requests through a local copilot-proxy instance.",
    )
    parser.add_argument(
        "--copilot-proxy-dir",
        default=None,
        help=f"Path to the local copilot-proxy checkout. {AUTO_DETECTED_PROXY_DIR_HELP}",
    )
    parser.add_argument(
        "--copilot-proxy-port",
        type=int,
        default=18080,
        help="Local copilot-proxy port.",
    )
    parser.add_argument("--session-id", default=None, help="Resume an existing backend session id.")
    parser.add_argument("--max-rounds", type=int, default=500, help="Maximum primary-agent rounds.")
    parser.add_argument(
        "--max-no-progress-rounds",
        type=int,
        default=3,
        help="Stop if repeated rounds produce identical main summary.",
    )
    parser.add_argument(
        "--check",
        action="append",
        help="Acceptance shell command (repeatable). All checks must pass before completion.",
    )
    parser.add_argument(
        "--check-timeout-seconds",
        type=int,
        default=1200,
        help="Timeout per acceptance check command.",
    )
    parser.add_argument("--main-model", default=None, help="Primary agent model override.")
    parser.add_argument("--reviewer-model", default=None, help="Reviewer sub-agent model override.")
    parser.add_argument("--plan-model", "--planner-model", dest="plan_model", default=None, help="Plan agent model override.")
    parser.add_argument(
        "--main-reasoning-effort",
        default=None,
        choices=["low", "medium", "high", "xhigh"],
        help="Primary agent reasoning effort override.",
    )
    parser.add_argument(
        "--reviewer-reasoning-effort",
        default=None,
        choices=["low", "medium", "high", "xhigh"],
        help="Reviewer sub-agent reasoning effort override.",
    )
    parser.add_argument(
        "--plan-reasoning-effort",
        "--planner-reasoning-effort",
        dest="plan_reasoning_effort",
        default=None,
        choices=["low", "medium", "high", "xhigh"],
        help="Plan agent reasoning effort override.",
    )
    parser.add_argument(
        "--main-extra-arg",
        action="append",
        help="Extra argument passed to the main backend command (repeatable).",
    )
    parser.add_argument(
        "--reviewer-extra-arg",
        action="append",
        help="Extra argument passed to the reviewer backend command (repeatable).",
    )
    parser.add_argument(
        "--plan-extra-arg",
        "--planner-extra-arg",
        dest="plan_extra_arg",
        action="append",
        help="Extra argument passed to the planner backend command (repeatable).",
    )
    parser.add_argument(
        "--plan-mode",
        "--planner-mode",
        dest="plan_mode",
        default="auto",
        choices=["off", "auto", "record"],
        help="Planner mode: off, auto, or record.",
    )
    parser.add_argument(
        "--planner",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable the planner sub-agent. Disabled maps to --plan-mode off.",
    )
    parser.add_argument(
        "--plan-update-interval-seconds",
        type=int,
        default=1800,
        help="Reserved compatibility flag for daemon-launched runs.",
    )
    parser.add_argument(
        "--follow-up-phase",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--skip-git-repo-check",
        action="store_true",
        help="Pass through when supported by the selected backend.",
    )
    parser.add_argument(
        "--full-auto",
        action="store_true",
        help="Request automatic tool approval mode when supported by the selected backend.",
    )
    parser.add_argument(
        "--yolo",
        action="store_true",
        help="Request the selected backend's highest-permission autonomous mode.",
    )
    parser.add_argument("--state-file", default=None, help="Write state JSON after each loop round.")
    parser.add_argument(
        "--operator-messages-file",
        default=None,
        help="Markdown document path for operator message history used by reviewer.",
    )
    parser.add_argument(
        "--plan-overview-file",
        "--plan-report-file",
        dest="plan_overview_file",
        default=None,
        help="Markdown document path for the plan agent overall summary.",
    )
    parser.add_argument(
        "--plan-todo-file",
        default=None,
        help="Optional planner TODO markdown path. When set, the current plan markdown is mirrored there.",
    )
    parser.add_argument(
        "--review-summaries-dir",
        default=None,
        help="Directory for per-round reviewer summary markdown files.",
    )
    parser.add_argument(
        "--final-report-file",
        default=None,
        help="Markdown file path for the final task report generated after reviewer DONE.",
    )
    parser.add_argument(
        "--main-prompt-file",
        default=None,
        help="Markdown file path for the latest main prompt sent to Codex.",
    )
    parser.add_argument(
        "--pptx-report",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable PPTX run report generation. When omitted, interactive runs prompt the user.",
    )
    parser.add_argument(
        "--pptx-report-file",
        default=None,
        help="Output path for the auto-generated PPTX run report.",
    )
    parser.add_argument(
        "--control-file",
        default=None,
        help="Local JSONL control file for terminal commands (inject/stop/status).",
    )
    parser.add_argument(
        "--control-poll-interval-seconds",
        type=int,
        default=1,
        help="Polling interval for local control file.",
    )
    parser.add_argument(
        "--stall-soft-idle-seconds",
        type=int,
        default=3600,
        help=(
            "Soft idle threshold in seconds. When exceeded without new output, "
            "stall sub-agent inspects recent messages and decides whether to restart."
        ),
    )
    parser.add_argument(
        "--stall-hard-idle-seconds",
        type=int,
        default=10800,
        help=(
            "Hard idle threshold in seconds. When exceeded without new output, "
            "force a restart regardless of sub-agent decision."
        ),
    )
    parser.add_argument(
        "--dashboard",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable/disable the local live dashboard while running.",
    )
    parser.add_argument("--dashboard-host", default="127.0.0.1", help="Dashboard host bind address.")
    parser.add_argument("--dashboard-port", type=int, default=8787, help="Dashboard TCP port.")
    parser.add_argument("--telegram-bot-token", default=None, help="Telegram bot token for progress messages.")
    parser.add_argument(
        "--telegram-chat-id",
        default="auto",
        help="Telegram chat id to receive messages. Use 'auto' to resolve from updates.",
    )
    parser.add_argument(
        "--telegram-events",
        default="loop.started,round.review.completed,loop.completed",
        help="Comma-separated event names to send to Telegram.",
    )
    parser.add_argument(
        "--telegram-timeout-seconds",
        type=int,
        default=10,
        help="HTTP timeout for Telegram API calls.",
    )
    parser.add_argument(
        "--telegram-chat-id-resolve-timeout-seconds",
        type=int,
        default=90,
        help="Seconds to poll getUpdates when resolving chat_id=auto.",
    )
    parser.add_argument(
        "--telegram-no-typing",
        action="store_true",
        help="Disable Telegram typing heartbeats during loop execution.",
    )
    parser.add_argument(
        "--telegram-typing-interval-seconds",
        type=int,
        default=4,
        help="Seconds between Telegram typing heartbeats.",
    )
    parser.add_argument(
        "--telegram-live-updates",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable batched live agent message push to Telegram.",
    )
    parser.add_argument(
        "--telegram-live-interval-seconds",
        type=int,
        default=30,
        help="Push interval for Telegram live updates; sends only when there are changes.",
    )
    parser.add_argument(
        "--telegram-control",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable Telegram inbound control commands.",
    )
    parser.add_argument(
        "--telegram-control-poll-interval-seconds",
        type=int,
        default=2,
        help="Polling interval for Telegram control command loop.",
    )
    parser.add_argument(
        "--telegram-control-long-poll-timeout-seconds",
        type=int,
        default=20,
        help="Long-poll timeout for Telegram getUpdates control loop.",
    )
    parser.add_argument(
        "--telegram-control-plain-text-inject",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Treat plain text Telegram messages as injected instruction updates.",
    )
    parser.add_argument(
        "--telegram-control-whisper",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable Whisper transcription for Telegram voice/audio control messages.",
    )
    parser.add_argument(
        "--telegram-control-whisper-api-key",
        default=None,
        help="OpenAI API key for Whisper. Defaults to OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--telegram-control-whisper-model",
        default="whisper-1",
        help="OpenAI transcription model used for Telegram voice/audio messages.",
    )
    parser.add_argument(
        "--telegram-control-whisper-base-url",
        default="https://api.openai.com/v1",
        help="OpenAI-compatible API base URL for Whisper transcription.",
    )
    parser.add_argument(
        "--telegram-control-whisper-timeout-seconds",
        type=int,
        default=90,
        help="Timeout in seconds for Whisper transcription requests.",
    )
    parser.add_argument("--feishu-app-id", default=None, help="Feishu app id for control/notifications.")
    parser.add_argument("--feishu-app-secret", default=None, help="Feishu app secret for control/notifications.")
    parser.add_argument("--feishu-chat-id", default=None, help="Feishu chat id for control/notifications.")
    parser.add_argument(
        "--feishu-receive-id-type",
        default="chat_id",
        help="Feishu receive_id_type used for outgoing messages.",
    )
    parser.add_argument(
        "--feishu-events",
        default="loop.started,round.review.completed,loop.completed",
        help="Comma-separated event names to send to Feishu.",
    )
    parser.add_argument(
        "--feishu-timeout-seconds",
        type=int,
        default=10,
        help="HTTP timeout for Feishu API calls.",
    )
    parser.add_argument(
        "--feishu-live-updates",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable batched live agent message push to Feishu.",
    )
    parser.add_argument(
        "--feishu-live-interval-seconds",
        type=int,
        default=30,
        help="Push interval for Feishu live updates; sends only when there are changes.",
    )
    parser.add_argument(
        "--feishu-control",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable Feishu inbound control commands.",
    )
    parser.add_argument(
        "--feishu-control-poll-interval-seconds",
        type=int,
        default=2,
        help="Polling interval for Feishu control command loop.",
    )
    parser.add_argument(
        "--feishu-control-plain-text-inject",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Treat plain text Feishu messages as injected instruction updates.",
    )
    parser.add_argument(
        "--live-terminal",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable realtime terminal printing of agent messages.",
    )
    parser.add_argument(
        "--verbose-events",
        action="store_true",
        help="Print raw Codex JSONL and stderr lines while running.",
    )
    parser.add_argument(
        "--add-dir",
        action="append",
        help="Additional directory to allow tool access (repeatable).",
    )
    parser.add_argument(
        "--plugin-dir",
        action="append",
        help="Load plugins from a directory (repeatable).",
    )
    parser.add_argument(
        "--file",
        dest="file_specs",
        action="append",
        help="File resource to download. Format: file_id:relative_path (repeatable).",
    )
    parser.add_argument(
        "--worktree",
        dest="worktree_name",
        nargs="?",
        const="default",
        default=None,
        help="Create a new git worktree for this session (optionally specify a name).",
    )
    return parser


def resolve_plan_report_file(
    *,
    explicit_path: str | None,
    state_file: str | None,
) -> str | None:
    if explicit_path:
        return explicit_path
    if state_file:
        return str(Path(state_file).resolve().parent / "plan_report.md")
    return None


def resolve_plan_todo_file(
    *,
    explicit_path: str | None,
    state_file: str | None,
) -> str | None:
    if explicit_path:
        return explicit_path
    if state_file:
        return str(Path(state_file).resolve().parent / "plan_todo.md")
    return None


def resolve_main_prompt_file(*, state_file: str | None, control_file: str | None) -> str | None:
    if control_file:
        return str(Path(control_file).resolve().parent / "main_prompt.md")
    if state_file:
        return str(Path(state_file).resolve().parent / "main_prompt.md")
    return None


def _should_prompt_pptx() -> bool:
    """Return True when we should interactively ask about PPTX generation."""
    import sys
    return sys.stdin.isatty()


def _ask_pptx_report() -> bool:
    """Prompt the user to decide whether to generate a PPTX run report."""
    import sys
    try:
        answer = input("Generate a PPTX run report at the end? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        return False
    return answer in ("", "y", "yes")


def _mirror_plan_report_to_todo(*, report_path: object, todo_path: str) -> None:
    if not todo_path:
        return
    if not isinstance(report_path, str) or not report_path.strip():
        return
    src = Path(report_path)
    if not src.exists():
        return
    dst = Path(todo_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        return


if __name__ == "__main__":
    main()
