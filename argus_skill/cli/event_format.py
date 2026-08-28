"""Pure event-formatting helpers for argus-skill.

The terminal renderer and tests share these pure formatters.
No I/O, no Telegram, no logging. Inputs are plain dicts; outputs are
plain strings.
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Callable

from ..core.event_catalog import EventType, canonical_event_type
from ..core.operator_messages import uses_cjk
from ..core.secret_guard import redact_secrets_text
from ..life.mission_outcome import mission_outcome_class

# Canonical live events with dedicated presentation. Unknown/internal events
# fall back to ``[event.type]`` so they remain grep-able. ``match.info`` is an
# intentionally non-persisted matcher diagnostic retained for stderr sinks.
_EVENT_ICONS: dict[str, str] = {
    EventType.LOOP_START: "🚀",
    EventType.LOOP_DONE: "🏁",
    EventType.ROUND_START: "🔁",
    EventType.ROUND_MAIN_COMPLETED: "🔧",
    EventType.ROUND_REVIEW_COMPLETED: "🧑‍⚖️",
    EventType.ENGINEER_PROGRESS: "◆",
    EventType.LIFE_MISSION_STARTED: "▶",
    EventType.LIFE_MISSION_COMPLETED: "■",
    EventType.LIFE_MISSION_FAILED: "💥",
    EventType.LIFE_STATUS: "ℹ️",
    EventType.LIFE_PLANNER_VERDICT: "📋",
    "match.info": "🎯",
}


def _truncate_display(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _short_path(path: str) -> str:
    path = path.strip().strip("'\"")
    match = re.match(r"^/home/[^/]+/([^/]+)$", path)
    if match:
        return match.group(1)
    path = re.sub(r"^/home/[^/]+/[^/]+/", "", path)
    path = re.sub(r"^\./", "", path)
    if len(path) > 90:
        path = path[:30].rstrip("/") + "…/" + path[-55:].lstrip("/")
    return path


def _safe_split(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _drop_shell_tail(text: str) -> str:
    text = text.strip().strip("'\"")
    text = re.split(r"\s+(?:2?>|1>|&>|\|)\s*", text, maxsplit=1)[0].strip()
    return text.strip().strip("'\"")


def _strip_shell_wrapper(raw: str) -> str:
    command = (raw or "").strip()
    parts = _safe_split(command)
    if len(parts) >= 3:
        executable = parts[0].rsplit("/", 1)[-1]
        flags = parts[1]
        if executable in {"bash", "sh", "zsh"} and flags.startswith("-") and "c" in flags:
            return parts[2].strip()
    match = re.match(
        r"/bin/(?:ba)?sh\s+-\w*c\s+(['\"])(.*)\1\s*$",
        command,
        re.DOTALL,
    )
    return match.group(2).strip() if match else command


def _split_shell_steps(command: str) -> list[str]:
    command = (command or "").strip()
    if not command:
        return []
    if re.search(
        r"\b(for|while|until|if|case)\b.*\b(do|then|in)\b",
        command,
        re.DOTALL,
    ):
        return [command]
    return [part.strip() for part in re.split(r"\s*(?:&&|\n)\s*", command) if part.strip()]


def _parse_simple_command(command: str) -> str:
    command = (command or "").strip()
    if not command or command.startswith(("printf ", "echo ___BEGIN___COMMAND_DONE_MARKER")):
        return ""
    if command.startswith("cd "):
        return f"📂 进入 {_short_path(command[3:].strip())}"
    if re.search(
        r"\b(for|while|until|if|case)\b.*\b(do|then|in)\b",
        command,
        re.DOTALL,
    ):
        return "🔧 执行 shell 脚本"

    match = re.match(
        r"nl\s+-ba\s+(.+?)\s*\|\s*sed\s+-n\s+'?(\d+),(\d+)p'?",
        command,
    )
    if match:
        path = _short_path(match.group(1).strip().strip("'\""))
        return f"📖 读取 {path}:{match.group(2)}-{match.group(3)}"

    match = re.match(r"sed\s+-n\s+'?(\d+),(\d+)p'?\s+(.+)", command)
    if match:
        path = _short_path(_drop_shell_tail(match.group(3)))
        return f"📖 读取 {path}:{match.group(1)}-{match.group(2)}"

    match = re.match(r"cat\s+(.+)", command)
    if match:
        return f"📖 读取 {_short_path(_drop_shell_tail(match.group(1)))}"

    if re.match(r"rg\s+--files\b", command):
        parts = _safe_split(command)
        path = next((part for part in parts[2:] if not part.startswith("-")), "")
        return f"📁 列文件 {_short_path(path)}" if path else "📁 列文件"

    if command.startswith(("rg ", "grep ")):
        match = re.search(r"""["']([^"']+)["']""", command)
        pattern = (
            match.group(1)
            if match
            else next(
                (part for part in command.split()[1:] if not part.startswith("-")),
                "…",
            )
        )
        return f"🔍 搜索 {pattern[:50]}"

    if command.startswith("find "):
        match = re.search(r"\s-(?:i)?name\s+(['\"]?)([^'\"\s|]+)\1", command)
        if not match:
            match = re.search(r"\s-path\s+(['\"]?)([^'\"\s|]+)\1", command)
        if match:
            return f"🔎 查找 {match.group(2)}"
        return f"🔎 查找文件 {_truncate_display(' '.join(_safe_split(command)[1:3]), 70)}"

    if command.startswith("ls "):
        paths = [part for part in _safe_split(command)[1:] if not part.startswith("-")]
        return f"📂 列目录 {_short_path(paths[-1]) if paths else '.'}"

    if re.match(r"(?:python|python3)\b", command):
        if "<<" in command:
            return "🐍 执行 Python 脚本"
        parts = _safe_split(command)
        if len(parts) >= 3 and parts[1:3] == ["-m", "pytest"]:
            return f"🧪 pytest {_truncate_display(' '.join(parts[3:]), 80)}".rstrip()
        if len(parts) >= 3 and parts[1] == "-m":
            return f"🐍 python -m {_truncate_display(' '.join(parts[2:]), 80)}"
        target = " ".join(parts[1:])
        return (
            f"🐍 执行 {_short_path(_truncate_display(target, 80))}" if target else "🐍 执行 Python"
        )

    if command.startswith("git "):
        parts = _safe_split(command)
        if len(parts) >= 4 and parts[1] == "-C":
            return (
                f"📦 git {_truncate_display(' '.join(parts[3:]), 70)} " f"({_short_path(parts[2])})"
            )
        return f"📦 {_truncate_display(command, 90)}"

    if command.startswith("pytest "):
        return f"🧪 {_truncate_display(command, 90)}"
    if command.startswith(("npm ", "pip ", "make ", "ruff ", "mypy ")):
        return f"🔧 {_truncate_display(command, 90)}"
    return f"▸ {_truncate_display(command, 90)}"


def format_progress_command(raw: str) -> str:
    """Render a shell command as one compact operator-facing action."""
    command = _strip_shell_wrapper(redact_secrets_text(raw))
    steps = _split_shell_steps(command)
    rendered = [item for item in (_parse_simple_command(step) for step in steps) if item]
    if len(rendered) > 1:
        if all(item.startswith("📖") for item in rendered):
            return f"📖 读取了 {len(rendered)} 个文件"
        preview = " → ".join(rendered[:3])
        if len(rendered) > 3:
            preview += " → …"
        return f"🔧 执行 {len(rendered)} 步：{_truncate_display(preview, 160)}"
    if rendered:
        return rendered[0]
    return _parse_simple_command(command) or "🔧 执行 shell 脚本"


def annotate_progress_result(line: str, event: dict[str, Any]) -> str:
    """Prefix command output with success/failure and append its short result."""
    status = str(event.get("status") or "").lower()
    exit_code = event.get("exit_code")
    failed = status == "failed" or (isinstance(exit_code, int) and exit_code not in (0, None))
    succeeded = status in {"completed", "succeeded", "success"} or (
        isinstance(exit_code, int) and exit_code == 0
    )
    if failed:
        line = "❌ " + line
    elif succeeded:
        line = "✅ " + line
    excerpt = str(event.get("output_excerpt") or "").strip()
    if excerpt:
        line += f" — {_truncate_display(excerpt, 140)}"
    return line


def format_event_message(event: dict[str, Any]) -> str:
    """Render one event after collapsing historical aliases to canonical types."""
    kind = canonical_event_type(event.get("type")) or "?"
    icon = _EVENT_ICONS.get(kind, "")
    renderer = _RICH_RENDERERS.get(kind)
    if renderer is not None:
        body = renderer(event)
        if not body:
            return icon or f"[{kind}]"
        return f"{icon} {body}".lstrip()

    text = str(event.get("text", "")).strip()
    if not text:
        return icon or f"[{kind}]"
    text = _trunc(text, 300 if icon else 200)
    return f"{icon} {text}".lstrip() if icon else f"[{kind}] {text}"


# ---------------------------------------------------------------------------
# Per-event-type rich renderers (LoopEngine + SkillLoopRunner payloads).
# ---------------------------------------------------------------------------


def _trunc(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[:n].rstrip() + "…"


def _round_label(event: dict[str, Any]) -> str:
    idx = event.get("round_index")
    return f"round {idx}" if idx is not None else "round ?"


def _render_loop_start(event: dict[str, Any]) -> str:
    objective = _trunc(str(event.get("objective") or ""), 120)
    if objective:
        details = []
        if event.get("max_rounds") is not None:
            details.append(f"max_rounds={event['max_rounds']}")
        if event.get("plan_mode"):
            details.append(f"plan_mode={event['plan_mode']}")
        suffix = f" — {', '.join(details)}" if details else ""
        return f"task: {objective}{suffix}"

    text = str(event.get("text") or "")
    marker = "## Live objective"
    if marker in text:
        live = text.split(marker, 1)[1].strip().lstrip(":").strip()
        return f"task: {_trunc(live, 200)}"
    line = next((line for line in text.splitlines() if line.strip()), "")
    return f"task: {_trunc(line, 200)}" if line else "task started"


def _render_round_start(event: dict[str, Any]) -> str:
    text = str(event.get("text") or "").strip()
    return _trunc(text, 160) if text else _round_label(event) + " starting…"


def _render_round_main_completed(event: dict[str, Any]) -> str:
    label = _round_label(event)
    last = _trunc(str(event.get("last_message") or ""), 800)
    fatal = (event.get("fatal_error") or "").strip()
    turn_completed = event.get("turn_completed")
    turn_failed = event.get("turn_failed")
    flags = []
    if turn_failed:
        flags.append("turn_failed")
    elif turn_completed is False:
        flags.append("incomplete")
    head = f"{label}: main agent finished"
    if flags:
        head += f" ({', '.join(flags)})"
    body = ""
    if last:
        body = f"\n   ↳ {last}"
    elif fatal:
        body = f"\n   ↳ ⚠ {_trunc(fatal, 300)}"
    return head + body


def _render_round_review_completed(event: dict[str, Any]) -> str:
    label = _round_label(event)
    status = str(event.get("status", "?"))
    reason = _trunc(str(event.get("reason") or ""), 400)
    next_action = _trunc(str(event.get("next_action") or ""), 200)
    outcome = {
        "done": "✅ verified",
        "continue": "↻ more work is needed",
        "blocked": "⛔ cannot continue yet",
        "replan_requested": "↺ the current route needs revision",
        "no_progress": "🚫 no useful change was found",
    }.get(status, "review finished")
    parts = [f"{label}: {outcome}"]
    if reason:
        parts.append(f"   ↳ {reason}")
    if next_action and status != "done":
        parts.append(f"   ↳ Next: {next_action}")
    return "\n".join(parts)


def _render_loop_done(event: dict[str, Any]) -> str:
    text = str(event.get("text") or "").strip()
    if "success" not in event:
        return _trunc(text, 200) if text else "loop done"
    head = "loop done — success" if event.get("success") else "loop done — FAILED"
    reason = _trunc(str(event.get("stop_reason") or ""), 400)
    return head + (f"\n   ↳ {reason}" if reason else "")


_PROGRESS_KIND_BADGE = {
    "agent_message": "💬",
    "assistant_message": "💬",
    "reasoning": "🤔",
    "command_execution": "$",
    "tool_use": "🔧",
    "file_change": "📝",
}


def _render_engineer_progress(event: dict[str, Any]) -> str:
    """Live agent-CLI stream beat — one item per call."""
    kind = str(event.get("kind") or "message").strip()
    text = redact_secrets_text(str(event.get("text") or "")).strip()
    badge = _PROGRESS_KIND_BADGE.get(kind, "•")
    if not text:
        return f"{badge} {kind}"
    # Already truncated upstream to 600 chars; trim further for chat scroll.
    text = _trunc(text, 240)
    if "\n" in text:
        # Keep the first non-blank line as the headline; show line count.
        lines = [ln for ln in text.splitlines() if ln.strip()]
        head = lines[0] if lines else text
        more = len(lines) - 1
        head = _trunc(head, 200)
        if more > 0:
            return f"{badge} {head}  (+{more} line{'s' if more != 1 else ''})"
        return f"{badge} {head}"
    return f"{badge} {text}"


def _render_life_mission_started(event: dict[str, Any]) -> str:
    title = (event.get("title") or event.get("objective") or "").strip()
    if title:
        return f"mission start — {_trunc(title, 100)}"
    return "mission start"


def _render_life_mission_completed(event: dict[str, Any]) -> str:
    title = str(event.get("title") or event.get("objective") or "current task").strip()
    summary = str(
        event.get("summary")
        or event.get("stop_reason")
        or event.get("failure_reason")
        or event.get("reason")
        or ""
    ).strip()
    status = str(event.get("status") or "").strip().lower()
    success = event.get("success") is True
    outcome_class = str(event.get("outcome_class") or "").strip().lower()
    if not outcome_class:
        outcome_class = mission_outcome_class(status, success)
    outcome = event.get("outcome")
    resumable = bool(event.get("resumable") or event.get("recoverable"))
    if isinstance(outcome, dict):
        resumable = resumable or outcome.get("resumable") is True
    chinese = uses_cjk(f"{title}\n{summary}")
    if success or outcome_class == "completed":
        headline = f"已完成：{title}。" if chinese else f"Completed: {title}."
    elif status.startswith("paused_") or resumable:
        headline = f"已暂停：{title}。" if chinese else f"Paused: {title}."
    elif outcome_class == "failed":
        headline = f"未能完成 {title}。" if chinese else f"Could not complete {title}."
    else:
        headline = (
            f"仍有工作未完成：{title}。"
            if chinese
            else f"Work remains on {title}."
        )
    if summary:
        headline += f" {summary}"

    metrics: list[str] = []
    rounds = event.get("rounds")
    if rounds is not None:
        metrics.append(
            f"{rounds} 轮"
            if chinese
            else f"{rounds} round{'s' if rounds != 1 else ''}"
        )
    elapsed = event.get("elapsed_seconds")
    if elapsed is None:
        elapsed = event.get("elapsed_s")
    if elapsed is not None:
        metrics.append(
            f"{float(elapsed):.1f} 秒" if chinese else f"{float(elapsed):.1f}s"
        )
    cost = event.get("cost_usd")
    pricing_status = str(event.get("pricing_status") or "")
    if cost is not None:
        suffix = "+" if pricing_status in {"partial", "unpriced"} else ""
        cost_text = f"${float(cost):.4f}{suffix}"
        metrics.append(f"成本 {cost_text}" if chinese else f"cost {cost_text}")
    elif pricing_status in {"partial", "unpriced"}:
        cost_text = "部分可用" if chinese else pricing_status
        metrics.append(f"成本 {cost_text}" if chinese else f"cost {cost_text}")
    return headline + ("\n" + " · ".join(metrics) if metrics else "")


_RICH_RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    EventType.LOOP_START: _render_loop_start,
    EventType.LOOP_DONE: _render_loop_done,
    EventType.ROUND_START: _render_round_start,
    EventType.ROUND_MAIN_COMPLETED: _render_round_main_completed,
    EventType.ROUND_REVIEW_COMPLETED: _render_round_review_completed,
    EventType.ENGINEER_PROGRESS: _render_engineer_progress,
    EventType.LIFE_MISSION_STARTED: _render_life_mission_started,
    EventType.LIFE_MISSION_COMPLETED: _render_life_mission_completed,
}


__all__ = [
    "annotate_progress_result",
    "format_event_message",
    "format_progress_command",
]
