from __future__ import annotations

from pathlib import Path
from typing import Any

from ..final_report import resolve_final_report_file as _resolve_final_report_file


def parse_telegram_events(raw: str) -> set[str]:
    values = [item.strip() for item in raw.split(",")]
    return {item for item in values if item}


def looks_like_bot_token(token: str) -> bool:
    if ":" not in token:
        return False
    left, right = token.split(":", 1)
    return left.isdigit() and len(right) >= 10


def resolve_operator_messages_file(
    *,
    explicit_path: str | None,
    control_file: str | None,
    state_file: str | None,
    default_root: str | None = None,
) -> str | None:
    if explicit_path:
        return explicit_path
    if control_file:
        return str(Path(control_file).resolve().parent / "operator_messages.md")
    if state_file:
        return str(Path(state_file).resolve().parent / "operator_messages.md")
    if default_root:
        return str(Path(default_root).resolve() / "operator_messages.md")
    return None


def resolve_plan_overview_file(
    *,
    explicit_path: str | None,
    operator_messages_file: str | None,
    control_file: str | None,
    state_file: str | None,
    default_root: str | None = None,
) -> str:
    if explicit_path:
        return explicit_path
    base = _resolve_artifact_dir(
        operator_messages_file=operator_messages_file,
        control_file=control_file,
        state_file=state_file,
        default_root=default_root,
    )
    return str(base / "plan_overview.md")


def resolve_review_summaries_dir(
    *,
    explicit_path: str | None,
    operator_messages_file: str | None,
    control_file: str | None,
    state_file: str | None,
    default_root: str | None = None,
) -> str:
    if explicit_path:
        return explicit_path
    base = _resolve_artifact_dir(
        operator_messages_file=operator_messages_file,
        control_file=control_file,
        state_file=state_file,
        default_root=default_root,
    )
    return str(base / "review_summaries")


def resolve_btw_messages_file(
    *,
    explicit_path: str | None,
    operator_messages_file: str | None,
    control_file: str | None,
    state_file: str | None,
    default_root: str | None = None,
) -> str:
    if explicit_path:
        return explicit_path
    base = _resolve_artifact_dir(
        operator_messages_file=operator_messages_file,
        control_file=control_file,
        state_file=state_file,
        default_root=default_root,
    )
    return str(base / "btw_messages.md")


def resolve_final_report_file(
    *,
    explicit_path: str | None,
    review_summaries_dir: str | None,
    operator_messages_file: str | None,
    control_file: str | None,
    state_file: str | None,
    default_root: str | None = None,
) -> str:
    return _resolve_final_report_file(
        explicit_path=explicit_path,
        review_summaries_dir=review_summaries_dir,
        operator_messages_file=operator_messages_file,
        control_file=control_file,
        state_file=state_file,
        default_root=default_root,
    )


def resolve_pptx_report_file(
    *,
    explicit_path: str | None,
    operator_messages_file: str | None,
    control_file: str | None,
    state_file: str | None,
    default_root: str | None = None,
) -> str:
    if explicit_path:
        return explicit_path
    base = _resolve_artifact_dir(
        operator_messages_file=operator_messages_file,
        control_file=control_file,
        state_file=state_file,
        default_root=default_root,
    )
    return str(base / "run-report.pptx")


def format_control_status(state: dict[str, Any]) -> str:
    status = state.get("status", "unknown")
    round_index = state.get("round", 0)
    session_id = state.get("session_id")
    success = state.get("success")
    stop_reason = state.get("stop_reason")
    plan_mode = state.get("plan_mode")
    latest_plan_next_explore = state.get("latest_plan_next_explore")
    main_prompt_file = state.get("main_prompt_file")
    plan_overview_file = state.get("plan_overview_file")
    review_summaries_dir = state.get("review_summaries_dir")
    final_report_file = state.get("final_report_file")
    final_report_ready = state.get("final_report_ready")
    pptx_report_file = state.get("pptx_report_file")
    pptx_report_ready = state.get("pptx_report_ready")
    lines = [
        "[autoloop] status",
        f"status={status}",
        f"round={round_index}",
        f"session_id={session_id}",
    ]
    if plan_mode:
        lines.append(f"plan_mode={plan_mode}")
    if latest_plan_next_explore:
        lines.append(f"latest_plan_next_explore={latest_plan_next_explore}")
    if success is not None:
        lines.append(f"success={success}")
    if stop_reason:
        lines.append(f"stop_reason={stop_reason}")
    if main_prompt_file:
        lines.append(f"main_prompt_file={main_prompt_file}")
    if plan_overview_file:
        lines.append(f"plan_overview_file={plan_overview_file}")
    if review_summaries_dir:
        lines.append(f"review_summaries_dir={review_summaries_dir}")
    if final_report_file:
        lines.append(f"final_report_file={final_report_file}")
    if final_report_ready is not None:
        lines.append(f"final_report_ready={final_report_ready}")
    if pptx_report_file:
        lines.append(f"pptx_report_file={pptx_report_file}")
    if pptx_report_ready is not None:
        lines.append(f"pptx_report_ready={pptx_report_ready}")
    return "\n".join(lines)


def control_help_text() -> str:
    return (
        "[autoloop] control commands\n"
        "/status - show loop status\n"
        "/inject <instruction> - interrupt main agent and apply new instruction\n"
        "/mode - show a mode selection menu\n"
        "/mode <off|auto|record> - hot-switch the current plan mode\n"
        "/btw <question> - ask the side-agent a read-only question about the current project\n"
        "/confirm-send - confirm and continue sending a pending large attachment batch\n"
        "/cancel-send - cancel a pending large attachment batch\n"
        "/plan <session-goal> - confirm the current session-level goal for planning\n"
        "/review <criteria> - send audit criteria to the reviewer only\n"
        "/show-main-prompt - print the latest main prompt markdown\n"
        "/show-plan - print the current plan overview markdown\n"
        "/show-plan-context - print current planner directions and inputs\n"
        "/show-review [round] - print review summaries markdown (latest index or a specific round)\n"
        "/show-review-context - print current reviewer direction, checks, and criteria\n"
        "/stop - interrupt and stop loop\n"
        "/help - show command help\n"
        "[CN] 默认不会自动续跑。若要启用 auto planning / auto follow-up，请先用 /plan 确认本 session 总目标。\n"
        "[EN] Auto follow-up is disabled by default. To enable auto planning/follow-up, confirm the session-level goal first with /plan.\n"
        "Plain text message is treated as instruction inject by default.\n"
        "Voice/audio message will be transcribed by Whisper when enabled."
    )


def format_mode_menu(current_mode: str) -> str:
    return (
        "[autoloop] choose plan mode / 选择规划模式\n"
        f"current={current_mode}\n"
        "1. off - disable plan agent / 关闭 planner\n"
        "2. auto - planner active, but auto follow-up requires /plan confirmation / 开启 planner，但自动续跑需要先 /plan 确认目标\n"
        "3. record - planner records only / 仅记录不自动续跑\n"
        "Reply with 1, 2, or 3. / 回复 1、2 或 3。"
    )


def _resolve_artifact_dir(
    *,
    operator_messages_file: str | None,
    control_file: str | None,
    state_file: str | None,
    default_root: str | None = None,
) -> Path:
    if operator_messages_file:
        return Path(operator_messages_file).resolve().parent
    if control_file:
        return Path(control_file).resolve().parent
    if state_file:
        return Path(state_file).resolve().parent
    if default_root:
        return Path(default_root).resolve()
    return Path(".").resolve() / ".argusbot"
