"""Durable, idempotent background messages shown in the operator conversation."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..life.event_log import JsonlEventSink
from .mission_view import say, uses_cjk
from .stop_kinds import park_reason_of
from .transcript import append_turn

_TIMEOUT_RE = re.compile(
    r"(?:timed? out|timeout|exceeded timeout_s)[^0-9]*(\d+(?:\.\d+)?)?",
    flags=re.IGNORECASE,
)


def humanize_runtime_reason(reason: str, *, language_hint: str = "", interactive: bool = False) -> str:
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
    from .autonomy import AUTOMATIC_RETRY_WINDOW_HOURS, RETRY_WINDOW_SPENT_REASON

    if raw.startswith(RETRY_WINDOW_SPENT_REASON):
        return (f"模型服务连续 {AUTOMATIC_RETRY_WINDOW_HOURS} 小时没有恢复，Argus 已停止自动重试，在你点击“运行”前不会开始新的任务。" if zh else
                f"The model service did not recover during {AUTOMATIC_RETRY_WINDOW_HOURS} hours of automatic retries, so Argus "
                "stopped retrying and starts no new task until you click Run.")
    if "trial_budget_exhausted" in lowered or "试用额度不足" in raw:
        return ("试用额度不足，任务已暂停。已有文件会保留，请联系管理员。" if zh else
                "The trial allowance is insufficient. Work is paused and existing files are preserved. Contact the trial administrator.")
    if "efbig" in lowered or "package extraction" in lowered:
        return ("执行环境未能准备所需文件，请联系管理员检查环境后重试。" if zh else
                "The runtime could not prepare its required files. Ask the administrator to check the environment before retrying.")
    if "process exited with code" in lowered or "before turn completion" in lowered:
        return ("模型运行中断，未能完成请求。请重试；如果仍然失败，请联系管理员。" if zh else
                "The model stopped before completing the request. Retry, or contact the administrator if it keeps failing.")
    timeout = _TIMEOUT_RE.search(raw)
    if timeout:
        seconds = timeout.group(1)
        duration = f"{seconds} 秒" if zh and seconds else f"{seconds} seconds" if seconds else "the time limit"
        if interactive:
            return (
                f"这次请求在 {duration} 内没有完成。请重试；如果仍然失败，请联系管理员。"
                if zh else
                f"This request did not finish within {duration}. Retry, or contact the administrator if it keeps failing."
            )
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


def brief_title(text: str, limit: int = 50) -> str:
    """Name a task in a sentence by its opening clause, never its whole objective."""
    title = " ".join(str(text or "").split()).rstrip("。.…")
    for marks in ("。！？；;!?", "，,：:"):
        if len(title) <= limit:
            break
        cut = next((
            i for i, ch in enumerate(title)
            if ch in marks and i >= 4 and title[i + 1:i + 3] != "//"
        ), 0)
        if cut:
            title = title[:cut]
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def render_operator_update(
    *,
    title: str,
    status: str,
    park_reason: str = "",
    reason: str = "",
    next_action: str = "",
    user_action_required: bool = False,
    language_hint: str = "",
) -> str:
    """Render structured runtime state as a short, plain operator update."""
    chinese = uses_cjk(language_hint or title)
    subject = brief_title(title) or say("this_task", chinese)
    state = str(status or "").strip().lower()
    park = park_reason_of(state, park_reason)
    why = humanize_runtime_reason(reason, language_hint=language_hint or subject)
    # Reviewer/Manager next_action is already the actionable instruction. Do
    # not replace it with the generic explanation used for a raw error reason.
    action = str(next_action or "").strip()
    if park == "provider_fence":
        return "\n".join(part for part in (
            say("update_provider_fenced", chinese, subject=subject),
            why and say("update_reason", chinese, why=why),
            action and say("update_advice", chinese, action=action),
            say("update_run_to_continue", chinese),
        ) if part)
    finished = state in {"done", "completed", "success"}
    canceled = state in {"aborted", "cancelled", "canceled"}
    lines = [say(
        "update_done" if finished
        else "update_canceled" if canceled
        else "update_continuing" if state in {"continue", "running", "in_progress", "stage_continues"}
        else "update_stuck" if state == "blocked" or park is not None
        else "update_new_approach" if state in {"replan_requested", "revise"}
        else "update_not_finished",
        chinese, subject=subject,
    )]
    if why and not canceled:
        # A finished task's note is what it found, not why it stopped.
        lines.append(why if finished else say("update_reason", chinese, why=why))
    if action:
        lines.append(say("update_your_call" if user_action_required else "update_next", chinese, action=action))
    elif not (finished or canceled):
        lines.append(say("update_needs_decision" if user_action_required else "update_will_investigate", chinese))
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
    "brief_title",
    "humanize_runtime_reason",
    "publish_operator_message",
    "render_operator_update",
]
