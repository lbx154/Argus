"""Durable, idempotent background messages shown in the operator conversation."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..life.event_log import JsonlEventSink
from .transcript import append_turn

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_TIMEOUT_RE = re.compile(
    r"(?:timed? out|timeout|exceeded timeout_s)[^0-9]*(\d+(?:\.\d+)?)?",
    flags=re.IGNORECASE,
)


def uses_cjk(text: str) -> bool:
    """Return whether *text* indicates a CJK operator-language message."""
    return bool(_CJK_RE.search(str(text or "")))


def humanize_runtime_reason(reason: str, *, language_hint: str = "") -> str:
    """Translate common control-plane failures into useful operator prose.

    Keep domain evidence intact; only replace mechanical runtime wording that
    otherwise leaks implementation details without telling the operator what it
    means.
    """
    raw = str(reason or "").strip()
    if not raw:
        return ""
    zh = uses_cjk(language_hint)
    lowered = raw.casefold()
    timeout = _TIMEOUT_RE.search(raw)
    if timeout:
        seconds = timeout.group(1)
        duration = f"{seconds} 秒" if zh and seconds else f"{seconds} seconds" if seconds else "the time limit"
        return (
            f"这次运行在 {duration} 内没有完成；这不代表方案错误。Argus 会先检查任务规模，再做最小诊断。"
            if zh
            else f"The run did not finish within {duration}; that does not prove the idea is wrong. Argus will check the workload size and run the smallest useful diagnostic first."
        )
    if "stale decision" in lowered or "campaign changed since" in lowered:
        return (
            "任务状态已在后台更新；Argus 会使用最新状态继续处理你的选择。"
            if zh
            else "The task changed in the background. Argus will apply your choice to the latest state."
        )
    if "already owned by active session" in lowered or (
        "workdir" in lowered and "already" in lowered and "session" in lowered
    ):
        return (
            "另一个活动会话正在使用这个工作区；Argus 会复用或等待该会话，不会重复启动。"
            if zh
            else "Another active session is using this workspace. Argus will reuse or wait for it instead of starting a duplicate."
        )
    if any(token in lowered for token in ("authentication", "credential", "api key", "access token")):
        return (
            "继续执行需要你提供或确认访问凭证；Argus 不会读取或展示未授权的密钥。"
            if zh
            else "Continuing requires an access credential from you. Argus will not expose or guess credentials."
        )
    if "budget" in lowered and any(token in lowered for token in ("exceed", "cap", "limit", "pause")):
        return (
            "本次运行已达到预算上限，现有结果已保留；提高预算或缩小任务后可以继续。"
            if zh
            else "This run reached its budget limit. Existing work is preserved; increase the budget or narrow the task to continue."
        )
    return raw


def render_operator_update(
    *,
    title: str,
    status: str,
    reason: str = "",
    next_action: str = "",
    user_action_required: bool = False,
    language_hint: str = "",
) -> str:
    """Render structured runtime state as a short, plain operator update."""
    subject = str(title or "the current task").strip()
    state = str(status or "").strip().lower()
    chinese = uses_cjk(language_hint or subject)
    why = humanize_runtime_reason(reason, language_hint=subject)
    # Reviewer/Manager next_action is already the actionable instruction. Do
    # not replace it with the generic explanation used for a raw error reason.
    action = str(next_action or "").strip()
    if state in {"done", "completed", "success"}:
        first = f"已完成：{subject}。" if chinese else f"Completed: {subject}."
    elif state in {"aborted", "cancelled", "canceled"}:
        first = f"已取消：{subject}。" if chinese else f"Canceled: {subject}."
    elif state in {"continue", "running", "in_progress"}:
        first = f"正在继续：{subject}。" if chinese else f"Still working on {subject}."
    elif state in {"blocked", "infra_blocked", "paused_operator"}:
        first = f"暂时无法继续：{subject}。" if chinese else f"Cannot continue yet: {subject}."
    elif state in {"replan_requested", "revise"}:
        first = (
            f"当前方案需要调整：{subject}。"
            if chinese
            else f"The current route for {subject} needs to change."
        )
    else:
        first = f"未能完成：{subject}。" if chinese else f"Could not complete {subject}."
    lines = [first]
    if why and state not in {"aborted", "cancelled", "canceled"}:
        lines.append(("原因：" if chinese else "Reason: ") + why)
    if action:
        prefix = (
            "需要你决定："
            if chinese and user_action_required
            else "下一步："
            if chinese
            else "Your decision: "
            if user_action_required
            else "Next: "
        )
        lines.append(prefix + action)
    elif state not in {
        "done",
        "completed",
        "success",
        "aborted",
        "cancelled",
        "canceled",
    }:
        lines.append(
            "需要你决定后才能继续。"
            if chinese and user_action_required
            else "下一步：Argus 会诊断原因并选择可恢复的方案。"
            if chinese
            else "Your decision is needed before work can continue."
            if user_action_required
            else "Next: Argus will diagnose the failure and choose a safe next step."
        )
    return "\n".join(lines)


def publish_operator_message(
    life_dir: Path | str,
    *,
    text: str,
    message_id: str,
    event_fields: dict[str, Any] | None = None,
) -> bool:
    """Append one Argus transcript turn and matching live event exactly once."""
    if not append_turn(
        life_dir,
        "argus",
        text,
        message_id=message_id,
        metadata=event_fields,
    ):
        return False
    event = {
        "type": "ui.argus",
        "agent_layer": "manager",
        "message_id": message_id,
        "text": text,
    }
    event.update(event_fields or {})
    JsonlEventSink(None, life_dir=Path(life_dir)).append(event)
    return True


__all__ = [
    "humanize_runtime_reason",
    "publish_operator_message",
    "render_operator_update",
    "uses_cjk",
]
