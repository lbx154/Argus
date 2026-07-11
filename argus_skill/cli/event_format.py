"""Pure event-formatting helpers for argus-skill.

The terminal renderer and tests share these pure formatters.
No I/O, no Telegram, no logging. Inputs are plain dicts; outputs are
plain strings.
"""
from __future__ import annotations

import re
import shlex
from typing import Any, Callable

from ..core.event_catalog import EventType

# map fall back to ``[event.type]`` so internal/dev events stay grep-able
# in verbose mode.
_EVENT_ICONS: dict[str, str] = {
    "task.queued":      "📥",
    "task.started":     "🏃",
    "task.completed":   "✅",
    "task.skipped":     "⏭",
    "task.error":       "❌",
    "command.ack":      "✓",
    "command.error":    "⚠️",
    "command.unknown":  "❓",
    "daemon.started":   "🟢",
    "daemon.stopping":  "🛑",
    "help":             "ℹ️",
    "status.report":    "📊",
    "mission.started":  "🎯",
    "mission.completed": "🎉",
    "mission.error":    "💥",
    "mission.idle":     "🟦",
    "loop.started":     "🚀",
    "loop.completed":   "🏁",
    "round.started":    "🔁",
    EventType.ROUND_MAIN_COMPLETED:   "🔧",
    "round.checks.completed": "🔍",
    EventType.ROUND_REVIEW_COMPLETED: "🧑‍⚖️",
    "round.control.injected": "💉",
    "round.watchdog.checked":          "🐶",
    "round.watchdog.restart_requested": "🔄",
    "plan.completed":   "📋",
    "match.info":       "🎯",
    "skill.writeback":  "💾",
    "pptx.report.ready": "📊",
    "final.report.ready": "📄",
    "distill.start":    "🧬",
    "distill.done":     "🧬",
    # Life-mode lifecycle (in-process REPL surfaces these).
    EventType.LIFE_MISSION_STARTED:   "▶",
    EventType.LIFE_MISSION_COMPLETED: "■",
    EventType.LIFE_STATUS:            "ℹ️",
    # SkillLoop legacy lifecycle (used by life mode's runner).
    EventType.LOOP_START:  "🚀",
    EventType.LOOP_DONE:   "🏁",
    EventType.ROUND_START: "🔁",
    # Live codex/claude/copilot stream progress (one beat per
    # ``item.completed`` JSON event the backend emits).
    EventType.ENGINEER_PROGRESS: "◆",
    # Repeated-tool-failure interrupt: the failed-tool ledger fires this
    # at most once per tool per mission when the agent is detected to be
    # blind-retrying a failing operation. High-signal — user should see it.
    "engineer.failure_nudge": "⚠",
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
    return [
        part.strip()
        for part in re.split(r"\s*(?:&&|\n)\s*", command)
        if part.strip()
    ]


def _parse_simple_command(command: str) -> str:
    command = (command or "").strip()
    if not command or command.startswith(
        ("printf ", "echo ___BEGIN___COMMAND_DONE_MARKER")
    ):
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
            f"🐍 执行 {_short_path(_truncate_display(target, 80))}"
            if target
            else "🐍 执行 Python"
        )

    if command.startswith("git "):
        parts = _safe_split(command)
        if len(parts) >= 4 and parts[1] == "-C":
            return (
                f"📦 git {_truncate_display(' '.join(parts[3:]), 70)} "
                f"({_short_path(parts[2])})"
            )
        return f"📦 {_truncate_display(command, 90)}"

    if command.startswith("pytest "):
        return f"🧪 {_truncate_display(command, 90)}"
    if command.startswith(("npm ", "pip ", "make ", "ruff ", "mypy ")):
        return f"🔧 {_truncate_display(command, 90)}"
    return f"▸ {_truncate_display(command, 90)}"


def format_progress_command(raw: str) -> str:
    """Render a shell command as one compact operator-facing action."""
    command = _strip_shell_wrapper(raw)
    steps = _split_shell_steps(command)
    rendered = [
        item for item in (_parse_simple_command(step) for step in steps) if item
    ]
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
    failed = status == "failed" or (
        isinstance(exit_code, int) and exit_code not in (0, None)
    )
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
    """Render a structured event as a short human-friendly string.

    LoopEngine and SkillLoopRunner emit events with rich structured payloads
    (``round_index``, ``status``, ``reason``, ``last_message``, …) instead of
    a single ``text`` field, so we dispatch to per-type renderers that pick
    out the most useful pieces.  Events without a custom renderer fall back
    to the legacy ``icon + text`` shape, and totally-unknown events keep the
    bracketed form so they remain grep-able in verbose mode.
    """
    kind = str(event.get("type", "?"))
    icon = _EVENT_ICONS.get(kind, "")

    # Per-event renderer: receives the full event dict, returns body text
    # (without the leading icon).
    renderer = _RICH_RENDERERS.get(kind)
    if renderer is not None:
        body = renderer(event)
        if not body:
            return icon or f"[{kind}]"
        return f"{icon} {body}".lstrip()

    # Legacy / simple events with a free-form ``text`` field.
    text = str(event.get("text", "")).strip()
    if icon == "":
        # Internal / unknown event — keep bracketed form for grep-ability.
        if not text:
            return f"[{kind}]"
        if len(text) > 200:
            text = text[:200].rstrip() + "…"
        return f"[{kind}] {text}"
    if not text:
        return icon
    cap = 1500 if kind == "task.completed" else 300
    if len(text) > cap:
        text = text[:cap].rstrip() + "…"
    return f"{icon} {text}"


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


def _render_loop_started(event: dict[str, Any]) -> str:
    obj = _trunc(str(event.get("objective", "")), 120)
    parts = [f"loop started — max_rounds={event.get('max_rounds', '?')}"]
    plan_mode = event.get("plan_mode")
    if plan_mode:
        parts.append(f"plan_mode={plan_mode}")
    out = ", ".join(parts)
    if obj:
        out += f"\n   objective: {obj}"
    return out


def _render_round_started(event: dict[str, Any]) -> str:
    return _round_label(event) + " starting…"


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


def _render_round_checks_completed(event: dict[str, Any]) -> str:
    label = _round_label(event)
    checks = event.get("checks") or []
    if not checks:
        return f"{label}: (no acceptance checks configured)"
    passed = sum(1 for c in checks if c.get("passed"))
    failed = len(checks) - passed
    head = f"{label}: checks {passed} ✓ / {failed} ✗"
    if failed:
        # Show first failing command for quick diagnosis.
        for c in checks:
            if not c.get("passed"):
                cmd = _trunc(str(c.get("command") or ""), 80)
                head += f" — failed: {cmd} (exit {c.get('exit_code')})"
                break
    return head


def _render_round_review_completed(event: dict[str, Any]) -> str:
    label = _round_label(event)
    status = str(event.get("status", "?"))
    reason = _trunc(str(event.get("reason") or ""), 400)
    next_action = _trunc(str(event.get("next_action") or ""), 200)
    status_icon = {
        "done": "✅",
        "continue": "↻",
        "blocked": "⛔",
        "no_progress": "🚫",
    }.get(status, "•")
    head = f"{label}: review {status_icon} {status}"
    parts = [head]
    if reason:
        parts.append(f"   ↳ reason: {reason}")
    if next_action and status != "done":
        parts.append(f"   ↳ next: {next_action}")
    return "\n".join(parts)


def _render_plan_completed(event: dict[str, Any]) -> str:
    label = _round_label(event)
    plan_mode = event.get("plan_mode") or "?"
    follow_up = event.get("follow_up_required")
    main_inst = _trunc(str(event.get("main_instruction") or ""), 400)
    review_inst = _trunc(str(event.get("review_instruction") or ""), 200)
    next_explore = _trunc(str(event.get("next_explore") or ""), 200)
    flag = "" if follow_up is None else (
        " (follow-up needed)" if follow_up else " (no more follow-up)"
    )
    parts = [f"{label} plan ({plan_mode}){flag}"]
    if main_inst:
        parts.append(f"   ↳ main: {main_inst}")
    if next_explore:
        parts.append(f"   ↳ explore: {next_explore}")
    if review_inst:
        parts.append(f"   ↳ review: {review_inst}")
    return "\n".join(parts)


def _render_round_control_injected(event: dict[str, Any]) -> str:
    label = _round_label(event)
    instruction = _trunc(str(event.get("instruction") or ""), 400)
    return f"{label}: operator instruction injected\n   ↳ {instruction}"


def _render_round_watchdog_checked(event: dict[str, Any]) -> str:
    label = _round_label(event)
    idle = event.get("idle_seconds")
    should_restart = event.get("should_restart")
    reason = _trunc(str(event.get("reason") or ""), 200)
    matched = event.get("matched_pattern")
    suffix = f" idle={idle}s"
    if should_restart:
        suffix += " — RESTART"
    if matched:
        suffix += f" ({matched})"
    if reason and should_restart:
        suffix += f" — {reason}"
    return f"{label} watchdog:{suffix}"


def _render_round_watchdog_restart_requested(event: dict[str, Any]) -> str:
    label = _round_label(event)
    reason = _trunc(str(event.get("reason") or ""), 300)
    return f"{label}: watchdog → restart — {reason}"


def _render_loop_completed(event: dict[str, Any]) -> str:
    success = event.get("success")
    reason = _trunc(str(event.get("stop_reason") or ""), 400)
    head = "loop done — success" if success else "loop done — FAILED"
    if reason:
        head += f"\n   ↳ {reason}"
    return head


def _render_pptx_report_ready(event: dict[str, Any]) -> str:
    return f"pptx report ready: {event.get('path', '')} (via {event.get('generated_by', '?')})"


def _render_final_report_ready(event: dict[str, Any]) -> str:
    return f"final report ready: {event.get('path', '')} (via {event.get('generated_by', '?')})"


def _render_command_ack(event: dict[str, Any]) -> str:
    """Render command acknowledgements.

    For most acks we just show the short text. For ``/show`` responses
    (``show_kind`` present) we render the full body in a fenced block so
    the operator can read the prompt/plan/review verbatim.
    """
    text = str(event.get("text", "")).strip()
    show_kind = event.get("show_kind")
    if show_kind:
        # The body may be multi-line — wrap in a code fence and let the
        # caller decide whether to truncate further.
        if not text:
            return f"/show {show_kind}: (empty)"
        return f"/show {show_kind}:\n```\n{text}\n```"
    return text


def _render_status_report(event: dict[str, Any]) -> str:
    """Pass /status text through verbatim (multi-line, no truncation).

    The MissionDaemon now emits a multi-line snapshot with round/phase/
    last-verdict/recent events; chopping it at 300 chars (the legacy
    cap) would defeat the whole point.
    """
    return str(event.get("text", "")).rstrip()


_PROGRESS_KIND_BADGE = {
    "agent_message":      "💬",
    "assistant_message":  "💬",
    "reasoning":          "🤔",
    "command_execution":  "$",
    "tool_use":           "🔧",
    "file_change":        "📝",
}


def _render_engineer_progress(event: dict[str, Any]) -> str:
    """Live codex/claude/copilot stream beat — one item per call."""
    kind = str(event.get("kind") or "message").strip()
    text = str(event.get("text") or "").strip()
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
    parts: list[str] = []
    status = event.get("status")
    if status:
        parts.append(f"status={status}")
    rounds = event.get("rounds")
    if rounds is not None:
        parts.append(f"rounds={rounds}")
    elapsed = event.get("elapsed_seconds") or event.get("elapsed_s")
    if elapsed is not None:
        parts.append(f"elapsed={float(elapsed):.1f}s")
    cost = event.get("cost_usd")
    pricing_status = str(event.get("pricing_status") or "")
    if cost is not None:
        suffix = "+" if pricing_status in {"partial", "unpriced"} else ""
        parts.append(f"cost=${float(cost):.4f}{suffix}")
    elif pricing_status in {"partial", "unpriced"}:
        parts.append(f"cost={pricing_status}")
    if not parts:
        return "mission complete"
    return "mission complete  ·  " + "  ·  ".join(parts)


def _render_loop_start(event: dict[str, Any]) -> str:
    """Hide the giant memory-context dump; show only the live objective."""
    text = str(event.get("text") or "")
    # The supervisor prepends "### Memory context (non-authoritative) … ---
    # ## Live objective\n<objective>". Skip the prelude entirely.
    marker = "## Live objective"
    if marker in text:
        live = text.split(marker, 1)[1].strip()
        live = live.lstrip(":").strip()
        return f"task: {_trunc(live, 200)}"
    # Fallback: show the first non-blank line.
    line = next((ln for ln in text.splitlines() if ln.strip()), "")
    return f"task: {_trunc(line, 200)}" if line else "task started"


def _render_loop_done(event: dict[str, Any]) -> str:
    text = str(event.get("text") or "").strip()
    return _trunc(text, 200) if text else "loop done"


def _render_round_start(event: dict[str, Any]) -> str:
    text = str(event.get("text") or "").strip()
    return _trunc(text, 160) if text else "engineer round"


_RICH_RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "loop.started": _render_loop_started,
    "round.started": _render_round_started,
    EventType.ROUND_MAIN_COMPLETED: _render_round_main_completed,
    "round.checks.completed": _render_round_checks_completed,
    EventType.ROUND_REVIEW_COMPLETED: _render_round_review_completed,
    "round.control.injected": _render_round_control_injected,
    "round.watchdog.checked": _render_round_watchdog_checked,
    "round.watchdog.restart_requested": _render_round_watchdog_restart_requested,
    "plan.completed": _render_plan_completed,
    "loop.completed": _render_loop_completed,
    "pptx.report.ready": _render_pptx_report_ready,
    "final.report.ready": _render_final_report_ready,
    "command.ack": _render_command_ack,
    "status.report": _render_status_report,
    # Life mode + legacy SkillLoop:
    EventType.ENGINEER_PROGRESS: _render_engineer_progress,
    EventType.LIFE_MISSION_STARTED: _render_life_mission_started,
    EventType.LIFE_MISSION_COMPLETED: _render_life_mission_completed,
    EventType.LOOP_START: _render_loop_start,
    EventType.LOOP_DONE: _render_loop_done,
    EventType.ROUND_START: _render_round_start,
}


__all__ = [
    "format_event_message",
    "_trunc",
    "_EVENT_ICONS",
    "_RICH_RENDERERS",
]
