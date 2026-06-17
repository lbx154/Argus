"""Notification dispatcher for terminal life events.

When the supervisor finalises a mission (``mission_complete`` /
``mission_failed`` / ``mission_orphaned`` / ``budget_pause`` /
``auth_failure``) the operator should hear about it without having to
stare at the terminal. This module hands such journal entries off to
three optional channels:

* ``ARGUS_SKILL_NOTIFY_WEBHOOK``: HTTP POST the event JSON to a URL.
  Slack / Discord / generic webhooks all work — the payload is
  intentionally simple ``{kind, title, summary, ts, cost_usd}``. We
  use ``urllib`` so there's no extra dependency.
* ``ARGUS_SKILL_NOTIFY_CMD``: shell command that gets the same JSON on
  stdin. Operators can wire ``mail``, ``notify-send``, ``pagerduty``,
  custom scripts, etc.
* Telegram support is disabled by default. It is only used when
  ``ARGUS_SKILL_ENABLE_TELEGRAM=1`` is set alongside the bot token/chat id.

All are best-effort: any failure is logged at WARNING and dropped on
the floor so the supervisor never crashes because of a flaky pager.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import time
from typing import Any

log = logging.getLogger(__name__)

# Journal entry kinds that warrant operator escalation.
DEFAULT_NOTIFY_KINDS = frozenset({
    "mission_started",
    "mission_complete",
    "mission_failed",
    "mission_iterated",
    "mission_orphaned",
    "mission_requeued",
    "budget_pause",
    "auth_failure",
    "planner_cycle",
    "planner_retry",
    "planner_done",
    "phase_change",
})


def telegram_enabled() -> bool:
    """Return True only when Telegram is explicitly opted in.

    Bot tokens often linger in operator shells. The daemon must not start
    Telegram pollers or send Telegram messages just because those env vars
    exist; Argustest uses the dashboard/events as the default observability path.
    """
    return (os.environ.get("ARGUS_SKILL_ENABLE_TELEGRAM") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def dispatch_journal_entry(entry: Any, *, kinds: frozenset[str] = DEFAULT_NOTIFY_KINDS) -> None:
    """Send ``entry`` to the configured notification channels.

    ``entry`` may be a ``JournalEntry`` dataclass or a plain dict (the
    in-memory journal returns the dataclass; tests pass dicts).
    """
    payload = _payload_from_entry(entry)
    kind = str(payload.get("kind") or "")
    if kind not in kinds:
        return
    _post_webhook(payload)
    _run_cmd(payload)
    _post_telegram(payload)


def _payload_from_entry(entry: Any) -> dict[str, Any]:
    # Dataclass with .to_jsonable()?
    if hasattr(entry, "to_jsonable"):
        try:
            d = entry.to_jsonable()
            if isinstance(d, dict):
                return _shape(d)
        except Exception:  # noqa: BLE001
            pass
    # Already a dict?
    if isinstance(entry, dict):
        return _shape(entry)
    # Last-ditch: try to read attributes by name.
    out: dict[str, Any] = {}
    for k in ("id", "kind", "title", "summary", "ts", "cost_usd", "tags"):
        if hasattr(entry, k):
            out[k] = getattr(entry, k)
    return _shape(out)


def _shape(d: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "kind", "title", "summary", "ts", "cost_usd", "tags", "extra")
    return {k: d.get(k) for k in keep if k in d}


def _post_webhook(payload: dict[str, Any]) -> None:
    url = (os.environ.get("ARGUS_SKILL_NOTIFY_WEBHOOK") or "").strip()
    if not url:
        return
    try:
        import urllib.request

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "argus-skill"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            if resp.status >= 400:
                log.warning(
                    "notify webhook returned status=%d for kind=%s",
                    resp.status, payload.get("kind"),
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("notify webhook failed (%s: %s)", type(exc).__name__, exc)


def _run_cmd(payload: dict[str, Any]) -> None:
    cmd = (os.environ.get("ARGUS_SKILL_NOTIFY_CMD") or "").strip()
    if not cmd:
        return
    try:
        body = json.dumps(payload, ensure_ascii=False)
        subprocess.run(
            shlex.split(cmd),
            input=body.encode("utf-8"),
            timeout=10.0,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("notify cmd failed (%s: %s)", type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# Telegram Bot API
# ---------------------------------------------------------------------------

_KIND_LABELS: dict[str, tuple[str, str]] = {
    "mission_started":   ("🚀", "开始任务"),
    "mission_complete":  ("✅", "任务完成"),
    "mission_failed":    ("❌", "任务失败"),
    "mission_iterated":  ("🔁", "任务迭代中"),
    "mission_orphaned":  ("⚠️", "任务被回收"),
    "mission_requeued":  ("🔃", "任务已恢复"),
    "budget_pause":      ("💰", "预算暂停"),
    "auth_failure":      ("🔐", "认证失败"),
    "planner_cycle":     ("📋", "规划完成"),
    "planner_retry":     ("🔁", "规划继续"),
    "planner_done":      ("🏁", "项目完成"),
    "phase_change":      ("🔄", "层级切换"),
}

_LAYER_LABELS: dict[str, str] = {
    "engineer": "👷 工程师 (L1)",
    "reviewer": "👨‍🏫 审查员 (L2)",
    # critic layer removed,
    "planner":  "🧠 规划师 (L4)",
}

# HTML-escape helper
def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_telegram_message(payload: dict[str, Any]) -> str:
    """Build a human-readable Telegram message from a journal payload."""
    kind = payload.get("kind", "unknown")
    emoji, label = _KIND_LABELS.get(kind, ("🔔", kind))
    title = payload.get("title", "")
    summary = payload.get("summary", "")
    cost = payload.get("cost_usd")
    extra = payload.get("extra") or {}
    if not isinstance(extra, dict):
        extra = {}

    # Timestamp
    ts = payload.get("ts")
    if isinstance(ts, (int, float)):
        import datetime
        ts_str = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    else:
        ts_str = time.strftime("%H:%M:%S")

    # Header
    lines = [f"{emoji} <b>{label}</b>  {ts_str}", "━━━━━━━━━━━━━━━━"]

    # Agent layer
    layer = extra.get("agent_layer", "")
    layer_label = _LAYER_LABELS.get(layer, "")
    if layer_label:
        lines.append(layer_label)

    # Task title
    if title:
        t = _esc(title if len(title) <= 80 else title[:77] + "…")
        lines.append(f"📌 {t}")

    # Objective (from extra, if available)
    objective = extra.get("objective", "")
    if objective:
        obj_text = _esc(objective if len(objective) <= 200 else objective[:197] + "…")
        lines.append(f"🎯 {obj_text}")

    # Kind-specific details
    phase_status = extra.get("phase_status", "")
    if kind == "mission_started":
        pass  # objective line above is sufficient
    elif kind == "phase_change":
        if phase_status == "completed":
            # Show completion details
            details: list[str] = []
            tokens_in = extra.get("input_tokens", 0)
            tokens_out = extra.get("output_tokens", 0)
            if tokens_in or tokens_out:
                details.append(f"tokens: {tokens_in}→{tokens_out}")
            rounds = extra.get("rounds")
            if rounds:
                details.append(f"轮次: {rounds}")
            imp_count = extra.get("improvement_count")
            if imp_count is not None:
                details.append(f"改进: {imp_count}项")
            reason = extra.get("reason", "")
            if reason:
                details.append(_esc(reason[:100]))
            if details:
                lines.append(f"📊 {' · '.join(details)}")
    elif kind in ("mission_complete", "mission_failed", "mission_iterated"):
        _format_mission_details(lines, extra, summary)
    elif kind in ("planner_cycle", "planner_retry"):
        _format_planner_details(lines, summary)
    elif kind == "planner_done":
        if summary:
            lines.append(f"\n{_esc(summary[:300])}")
    elif kind == "budget_pause":
        if summary:
            lines.append(f"\n⏸️ {_esc(summary[:200])}")
    elif summary:
        s = _esc(summary if len(summary) <= 300 else summary[:297] + "…")
        lines.append(f"\n{s}")

    # Cost line
    # Show "本次" for phase_change only when completed (has real cost)
    cumul = extra.get("cumulative_cost_usd")
    cost_parts: list[str] = []
    show_per_item = kind not in ("mission_started",)
    if kind == "phase_change" and phase_status != "completed":
        show_per_item = False
    if cost is not None and show_per_item and float(cost) > 0:
        cost_parts.append(f"本次 ${float(cost):.4f}")
    if cumul is not None:
        cost_parts.append(f"累计 <b>${float(cumul):.2f}</b>")
    if cost_parts:
        lines.append(f"\n💵 {' · '.join(cost_parts)}")

    return "\n".join(lines)


def _format_mission_details(lines: list[str], extra: dict[str, Any], summary: str) -> None:
    """Add mission-specific progress info."""
    details: list[str] = []

    # Rounds
    # Parse from summary (format: "status=done; rounds=5; ...")
    for part in summary.split(";"):
        part = part.strip()
        if part.startswith("rounds="):
            try:
                rounds = int(part.split("=")[1])
                details.append(f"执行 {rounds} 轮")
            except (ValueError, IndexError):
                pass
        elif part.startswith("elapsed="):
            try:
                elapsed = part.split("=")[1]
                details.append(f"耗时 {elapsed}")
            except IndexError:
                pass

    # Iteration info
    iteration = extra.get("iteration") or {}
    if isinstance(iteration, dict):
        cycle = iteration.get("cycle") or iteration.get("cycles_done")
        max_c = iteration.get("max_cycles")
        if cycle:
            details.append(f"迭代 {cycle}/{max_c or '?'}")
        if iteration.get("requeued"):
            details.append("已重排队")

    if details:
        lines.append(f"\n📊 {' · '.join(details)}")


def _format_planner_details(lines: list[str], summary: str) -> None:
    """Add planner-specific task list."""
    # Summary format: "generated N task(s): title1, title2, ..."
    if ":" in summary:
        prefix, tasks_str = summary.split(":", 1)
        lines.append(f"\n{_esc(prefix.strip())}")
        tasks = [t.strip() for t in tasks_str.split(",") if t.strip()]
        for i, t in enumerate(tasks[:5], 1):
            lines.append(f"  {i}. {_esc(t[:60])}")
        if len(tasks) > 5:
            lines.append(f"  … 还有 {len(tasks) - 5} 个")
    elif summary:
        lines.append(f"\n{_esc(summary[:300])}")


def _post_telegram(payload: dict[str, Any]) -> None:
    if not telegram_enabled():
        return
    token = (os.environ.get("ARGUS_SKILL_TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("ARGUS_SKILL_TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return
    text = _format_telegram_message(payload)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, ensure_ascii=False).encode("utf-8")
    try:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            if resp.status >= 400:
                log.warning(
                    "telegram notify returned status=%d for kind=%s",
                    resp.status, payload.get("kind"),
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("telegram notify failed (%s: %s)", type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# Telegram live-streaming reporter
# ---------------------------------------------------------------------------

_PROGRESS_KIND_EMOJI: dict[str, str] = {
    "agent_message": "💭",
    "command_execution": "🔧",
    "reasoning": "🧠",
}


def _parse_command(raw: str) -> str:
    """Turn a raw shell command into a short, readable description."""
    cmd = _strip_shell_wrapper(raw)
    steps = _split_shell_steps(cmd)
    parsed_steps = [
        p for p in (_parse_simple_command(step) for step in steps) if p
    ]
    if len(parsed_steps) > 1:
        if all(p.startswith("📖") for p in parsed_steps):
            return f"📖 读取了 {len(parsed_steps)} 个文件"
        preview = " → ".join(parsed_steps[:3])
        if len(parsed_steps) > 3:
            preview += " → …"
        return f"🔧 执行 {len(parsed_steps)} 步：{_truncate_display(preview, 160)}"
    if parsed_steps:
        return parsed_steps[0]
    return _parse_simple_command(cmd) or "🔧 执行 shell 脚本"


def _strip_shell_wrapper(raw: str) -> str:
    """Peel the common ``/bin/bash -lc '...'`` wrapper without executing it."""
    cmd = (raw or "").strip()
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = []
    if len(parts) >= 3:
        exe = parts[0].rsplit("/", 1)[-1]
        flags = parts[1]
        if exe in {"bash", "sh", "zsh"} and flags.startswith("-") and "c" in flags:
            return parts[2].strip()

    m = re.match(r"/bin/(?:ba)?sh\s+-\w*c\s+(['\"])(.*)\1\s*$", cmd, re.DOTALL)
    if m:
        return m.group(2).strip()
    return cmd


def _split_shell_steps(cmd: str) -> list[str]:
    """Split simple command chains while leaving shell scripts intact."""
    cmd = (cmd or "").strip()
    if not cmd:
        return []
    if re.search(r"\b(for|while|until|if|case)\b.*\b(do|then|in)\b", cmd, re.DOTALL):
        return [cmd]
    return [
        part.strip()
        for part in re.split(r"\s*(?:&&|\n)\s*", cmd)
        if part.strip()
    ]


def _parse_simple_command(cmd: str) -> str:
    cmd = (cmd or "").strip()
    if not cmd:
        return ""
    if cmd.startswith(("printf ", "echo ___BEGIN___COMMAND_DONE_MARKER")):
        return ""
    if cmd.startswith("cd "):
        return f"📂 进入 {_short_path(cmd[3:].strip())}"
    if re.search(r"\b(for|while|until|if|case)\b.*\b(do|then|in)\b", cmd, re.DOTALL):
        return "🔧 执行 shell 脚本"

    # nl -ba FILE | sed -n 'N,Mp' → 📖 reading numbered FILE:N-M
    m = re.match(r"nl\s+-ba\s+(.+?)\s*\|\s*sed\s+-n\s+'?(\d+),(\d+)p'?", cmd)
    if m:
        path = _short_path(m.group(1).strip().strip("'\""))
        return f"📖 读取 {path}:{m.group(2)}-{m.group(3)}"

    # sed -n 'N,Mp' FILE → 📖 reading FILE:N-M
    m = re.match(r"sed\s+-n\s+'?(\d+),(\d+)p'?\s+(.+)", cmd)
    if m:
        path = _short_path(_drop_shell_tail(m.group(3)))
        return f"📖 读取 {path}:{m.group(1)}-{m.group(2)}"

    # cat FILE → 📖 reading FILE
    m = re.match(r"cat\s+(.+)", cmd)
    if m:
        return f"📖 读取 {_short_path(_drop_shell_tail(m.group(1)))}"

    # rg --files PATH → 📁 listing files
    if re.match(r"rg\s+--files\b", cmd):
        parts = _safe_split(cmd)
        path = next((p for p in parts[2:] if not p.startswith("-")), "")
        return f"📁 列文件 {_short_path(path)}" if path else "📁 列文件"

    # rg / grep → 🔍 searching PATTERN
    if cmd.startswith(("rg ", "grep ")):
        # Extract quoted pattern if possible
        m2 = re.search(r'''["']([^"']+)["']''', cmd)
        if m2:
            pat = m2.group(1)[:50]
        else:
            # Take first non-flag argument
            parts = cmd.split()
            pat = next((p for p in parts[1:] if not p.startswith("-")), "…")[:50]
        return f"🔍 搜索 {pat}"

    # find ... -name/-path PATTERN → 🔎 locating files/dirs
    if cmd.startswith("find "):
        m = re.search(r"\s-(?:i)?name\s+(['\"]?)([^'\"\s|]+)\1", cmd)
        if not m:
            m = re.search(r"\s-path\s+(['\"]?)([^'\"\s|]+)\1", cmd)
        if m:
            return f"🔎 查找 {m.group(2)}"
        roots = " ".join(_safe_split(cmd)[1:3])
        return f"🔎 查找文件 {_truncate_display(roots, 70)}"

    # ls PATH → 📂 listing directory
    if cmd.startswith("ls "):
        parts = [p for p in _safe_split(cmd)[1:] if not p.startswith("-")]
        path = _short_path(parts[-1]) if parts else "."
        return f"📂 列目录 {path}"

    # python / python3 → 🐍 running python script
    if re.match(r"(?:python|python3)\b", cmd):
        # Try to extract the module/script or first meaningful line
        if "<<" in cmd:
            return "🐍 执行 Python 脚本"
        parts = _safe_split(cmd)
        if len(parts) >= 3 and parts[1] == "-m" and parts[2] == "pytest":
            rest = " ".join(parts[3:])
            return f"🧪 pytest {_truncate_display(rest, 80)}".rstrip()
        if len(parts) >= 3 and parts[1] == "-m":
            rest = " ".join(parts[2:])
            return f"🐍 python -m {_truncate_display(rest, 80)}"
        target = " ".join(parts[1:]) if len(parts) > 1 else ""
        return f"🐍 执行 {_short_path(_truncate_display(target, 80))}" if target else "🐍 执行 Python"

    # git commands
    if cmd.startswith("git "):
        parts = _safe_split(cmd)
        if len(parts) >= 4 and parts[1] == "-C":
            repo = _short_path(parts[2])
            subcmd = " ".join(parts[3:])
            return f"📦 git {_truncate_display(subcmd, 70)} ({repo})"
        return f"📦 {_truncate_display(cmd, 90)}"

    # npm/pip/make/pytest/etc.
    if cmd.startswith("pytest "):
        return f"🧪 {_truncate_display(cmd, 90)}"
    for tool in ("npm", "pip", "make", "ruff", "mypy"):
        if cmd.startswith(tool):
            return f"🔧 {_truncate_display(cmd, 90)}"

    # Generic: just truncate
    short = _truncate_display(cmd, 90)
    return f"▸ {short}"


def _safe_split(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def _drop_shell_tail(text: str) -> str:
    text = text.strip().strip("'\"")
    text = re.split(r"\s+(?:2?>|1>|&>|\|)\s*", text, maxsplit=1)[0].strip()
    return text.strip().strip("'\"")


def _truncate_display(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _short_path(path: str) -> str:
    """Strip common prefixes to make paths readable."""
    path = path.strip().strip("'\"")
    m = re.match(r"^/home/[^/]+/([^/]+)$", path)
    if m:
        return m.group(1)
    # Remove /home/<user>/<project>/ prefix
    path = re.sub(r'^/home/[^/]+/[^/]+/', '', path)
    # Remove leading ./ or ./
    path = re.sub(r'^\./', '', path)
    if len(path) > 90:
        path = path[:30].rstrip("/") + "…/" + path[-55:].lstrip("/")
    return path


def _summarize_progress(items: list[dict[str, Any]]) -> list[str]:
    """Turn a list of progress events into readable action lines.

    Consecutive file reads are batched into a single summary line.
    Agent messages are always shown individually (they're the most valuable).
    """
    actions: list[str] = []
    pending_reads: list[str] = []

    def _flush_reads() -> None:
        if not pending_reads:
            return
        if len(pending_reads) == 1:
            actions.append(pending_reads[0])
        else:
            # Collapse N file reads into one line
            actions.append(f"📖 读取了 {len(pending_reads)} 个文件")
        pending_reads.clear()

    for ev in items:
        kind = ev.get("kind", "")
        text = ev.get("text", "")
        if not text:
            continue

        if kind == "agent_message":
            _flush_reads()
            # Agent thinking — the most valuable content
            msg = _truncate_display(text, 320)
            actions.append(f"💭 {msg}")
        elif kind == "reasoning":
            # Keep raw reasoning out of Telegram by default; agent messages
            # already provide operator-facing intent without scratchpad noise.
            if os.environ.get("ARGUS_SKILL_TELEGRAM_SHOW_REASONING", "").lower() in (
                "1", "true", "yes", "on",
            ):
                _flush_reads()
                msg = _truncate_display(text, 160)
                actions.append(f"🧠 {msg}")
        elif kind == "command_execution":
            parsed = _parse_command(text)
            parsed = _annotate_progress_result(parsed, ev)
            if parsed.startswith("📖"):
                pending_reads.append(parsed)
            else:
                _flush_reads()
                actions.append(parsed)
        elif kind == "file_change":
            _flush_reads()
            actions.append(_annotate_progress_result(
                f"📝 {_truncate_display(text, 120)}", ev,
            ))
        elif kind == "tool_result":
            _flush_reads()
            actions.append(f"📤 {_truncate_display(text, 160)}")
        elif kind == "phase":
            _flush_reads()
            actions.append(f"🔄 {_truncate_display(text, 120)}")
        elif kind == "lifecycle":
            _flush_reads()
            actions.append(_truncate_display(text, 220))
        else:
            _flush_reads()
            short = _truncate_display(text, 120)
            actions.append(f"▸ {short}")

    _flush_reads()
    deduped: list[str] = []
    for action in actions:
        if not deduped or deduped[-1] != action:
            deduped.append(action)
    return deduped


def _annotate_progress_result(line: str, ev: dict[str, Any]) -> str:
    status = str(ev.get("status") or "").lower()
    exit_code = ev.get("exit_code")
    failed = status == "failed" or (
        isinstance(exit_code, int) and exit_code not in (0, None)
    )
    succeeded = (
        status in {"completed", "succeeded", "success"}
        or (isinstance(exit_code, int) and exit_code == 0)
    )
    if failed:
        line = "❌ " + line
    elif succeeded:
        line = "✅ " + line

    excerpt = str(ev.get("output_excerpt") or "").strip()
    if excerpt:
        line += f" — {_truncate_display(excerpt, 140)}"
    return line


def _event_round(event: dict[str, Any]) -> str:
    value = event.get("round_index", event.get("round", "?"))
    return str(value if value not in (None, "") else "?")


def _event_int(event: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = event.get(key)
        if isinstance(value, bool) or value in (None, ""):
            continue
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str) and value.isdecimal():
            parsed = int(value)
        else:
            continue
        if parsed > 0:
            return parsed
    return None


def _event_round_number(event: dict[str, Any]) -> int | None:
    return _event_int(event, "round_index", "round")


def _event_round_max(event: dict[str, Any]) -> int | None:
    return _event_int(event, "round_max", "max_rounds")


def _round_progress_label(current: int | None, max_rounds: int | None) -> str:
    if current is None:
        return "?"
    if max_rounds:
        return f"{current}/{max_rounds}"
    return str(current)


def _format_confidence(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    return ""


def _l2_icon(status: str) -> str:
    normalized = status.lower()
    if normalized in {"done", "pass", "passed", "success"}:
        return "✅"
    if normalized in {"continue", "needs_changes", "retry"}:
        return "🔁"
    if normalized in {"blocked", "failed", "error"}:
        return "⛔"
    return "👨‍🏫"


def _format_review_verdict(event: dict[str, Any]) -> str:
    status = str(event.get("status") or "?")
    parts = [
        f"{_l2_icon(status)} L2 审查员 · verdict={status}",
        f"engineer round {_event_round(event)}",
    ]
    confidence = _format_confidence(event.get("confidence"))
    if confidence:
        parts.append(f"conf={confidence}")
    reason = str(event.get("reason") or "").strip()
    if reason:
        parts.append(f"reason={_truncate_display(reason, 120)}")
    next_action = str(event.get("next_action") or "").strip()
    if next_action:
        parts.append(f"next={_truncate_display(next_action, 120)}")
    return " · ".join(parts)


def _format_critic_verdict(event: dict[str, Any]) -> str:
    stop = bool(event.get("stop"))
    count = int(event.get("improvement_count") or 0)
    raw_count = int(event.get("raw_improvement_count") or count)
    dropped = int(event.get("dropped_low_value_count") or 0)
    verdict = "stop" if stop else "continue"
    parts = [
        ("✅" if stop else "🔁") + f" L3 评审员 · verdict={verdict}",
    ]
    if not stop:
        parts.append(f"high-value improvements={count}")
    elif raw_count:
        parts.append(f"accepted={count}/{raw_count}")
    if dropped:
        parts.append(f"dropped low-value={dropped}")
    reason = str(event.get("reason") or "").strip()
    if reason:
        parts.append(f"reason={_truncate_display(reason, 160)}")
    return " · ".join(parts)


def _format_iteration_continued(event: dict[str, Any]) -> str:
    cycle = event.get("cycles_done", "?")
    max_cycle = event.get("cycles_max", "?")
    improvements = event.get("improvements") or []
    titles: list[str] = []
    if isinstance(improvements, list):
        for improvement in improvements[:2]:
            if isinstance(improvement, dict):
                title = str(improvement.get("title") or "").strip()
                if title:
                    titles.append(_truncate_display(title, 60))
    suffix = f" · {', '.join(titles)}" if titles else ""
    return f"🔁 L3 已重排 · mission iteration {cycle}/{max_cycle}{suffix}"


_PRE_ENGINEER_PROGRESS_TYPES = frozenset({
    "loop.start",
    "match.info",
    "scientist.start",
    "distill.start",
    "distill.done",
    "skill.distill.warnings",
})


def _format_pre_engineer_progress(event: dict[str, Any]) -> str:
    etype = str(event.get("type") or "")
    text = str(event.get("text") or "")
    if etype == "loop.start":
        body = text
        for prefix in ("task:", "chat:"):
            if body.startswith(prefix):
                body = body[len(prefix):].strip()
                break
        if body:
            return f"🟢 开始处理：{_truncate_display(body, 120)}"
        return "🟢 开始处理"
    if etype == "match.info":
        if "querying matcher" in text:
            return "🧭 正在匹配可复用技能"
        picked = re.search(r"matcher picked:\s*([^()]+)", text)
        if picked:
            return f"🧭 匹配到技能：{_truncate_display(picked.group(1).strip(), 90)}"
        if "no high-fit" in text:
            return "🧭 没有合适技能，正在准备临时执行策略"
        return f"🧭 {_truncate_display(text, 180)}" if text else ""
    if etype in {"scientist.start", "distill.start"}:
        return "🧪 正在准备临时执行策略"
    if etype == "distill.done":
        return "🧪 临时执行策略准备完成"
    if etype == "skill.distill.warnings":
        return "🧪 临时执行策略已生成，有轻微质量提示"
    return ""


class TelegramStreamReporter:
    """Buffers ``engineer.progress`` events and sends readable Telegram
    messages as separate progress cards.

    All network I/O happens in a dedicated daemon thread to avoid
    blocking the event pipeline.  The public API (``on_event``,
    ``start_mission``, ``end_mission``) is thread-safe — callers just
    enqueue lightweight objects into a ``collections.deque``.

    Usage::

        reporter = TelegramStreamReporter()
        reporter.start()          # spawns the flush thread
        reporter.on_event(event)  # call from any thread
        reporter.stop()           # graceful shutdown
    """

    FLUSH_INTERVAL = 12  # seconds between fallback progress flushes
    MIN_FLUSH_INTERVAL = 1.5  # coalesce bursty replacement events
    MAX_MSG_LEN = 3800   # leave room for markup overhead

    def __init__(self, *, stop_event: Any = None) -> None:
        import collections
        import threading as _threading

        self._token = (os.environ.get("ARGUS_SKILL_TELEGRAM_BOT_TOKEN") or "").strip()
        self._chat_id = (os.environ.get("ARGUS_SKILL_TELEGRAM_CHAT_ID") or "").strip()
        self._stop = stop_event or _threading.Event()
        self._buf: collections.deque[dict[str, Any]] = collections.deque(maxlen=160)
        self._poke = _threading.Event()
        self._lock = _threading.Lock()
        self._mission_title: str = ""
        self._mission_layer: str = ""
        self._mission_start: float = 0.0
        self._event_count: int = 0
        self._last_flush_monotonic: float = 0.0
        self._thread: _threading.Thread | None = None
        self._enabled = telegram_enabled() and bool(self._token and self._chat_id)

    # -- public API (called from event thread) ----------------------------

    def start(self) -> None:
        if not self._enabled:
            return
        import threading as _threading
        t = _threading.Thread(target=self._run, name="tg-stream", daemon=True)
        self._thread = t
        t.start()

    def stop(self) -> None:
        self._stop.set()
        self._poke.set()
        if self._thread:
            self._thread.join(timeout=5)

    def on_event(self, event: dict[str, Any]) -> None:
        """Accept any event dict from the sink.  Only progress events
        are buffered; lifecycle events update mission state."""
        if not self._enabled:
            return
        etype = event.get("type", "")
        if etype == "engineer.progress":
            with self._lock:
                self._event_count += 1
                self._append_progress_locked(event)
            self._request_flush()
        elif etype == "life.mission.started":
            self.start_mission(
                title=event.get("title", ""),
                layer="engineer",
            )
        elif etype in ("life.mission.completed", "life.mission.failed"):
            status = "failed" if etype.endswith(".failed") else "done"
            self.end_mission(status=status)
        elif etype in _PRE_ENGINEER_PROGRESS_TYPES:
            text = _format_pre_engineer_progress(event)
            should_flush = False
            if text:
                with self._lock:
                    if self._mission_title:
                        self._event_count += 1
                        self._buf.append({"kind": "lifecycle", "text": text})
                        should_flush = True
            if should_flush:
                self._request_flush()
        elif etype == "life.phase.started":
            layer = str(event.get("agent_layer") or event.get("layer") or "")
            card = ""
            with self._lock:
                if layer:
                    self._mission_layer = layer
                    label = _LAYER_LABELS.get(layer, layer)
                    card = f"🔄 <b>层级切换</b>\n当前：{_esc(label)}"
            if card:
                self._send_message(card)
            self._request_flush()
        elif etype == "round.start":
            round_number = _event_round_number(event)
            round_max = _event_round_max(event)
            round_label = _round_progress_label(round_number, round_max)
            raw_text = str(event.get("text") or "")
            with self._lock:
                self._mission_layer = "engineer"
                card = self._format_round_start_card_locked(round_label, raw_text)
            self._send_message(card)
            self._request_flush()
        elif etype == "round.main.completed":
            failed = bool(event.get("fatal_error")) or (
                isinstance(event.get("exit_code"), int)
                and event.get("exit_code") not in (0, None)
            )
            round_number = _event_round_number(event)
            round_max = _event_round_max(event)
            round_label = _round_progress_label(round_number, round_max)
            with self._lock:
                self._mission_layer = "engineer"
                card = self._format_round_completed_card_locked(event, round_label, failed)
            self._send_message(card)
            self._request_flush()
        elif etype == "round.review.started":
            round_number = _event_round_number(event)
            round_max = _event_round_max(event)
            round_label = _round_progress_label(round_number, round_max)
            with self._lock:
                self._mission_layer = "reviewer"
                card = self._format_review_started_card_locked(round_label)
            self._send_message(card)
            self._request_flush()
        elif etype == "round.review.completed":
            with self._lock:
                self._mission_layer = "reviewer"
                card = self._format_review_card_locked(event)
            self._send_message(card)
            self._request_flush()
        elif etype == "life.iteration.critic":
            text = _format_critic_verdict(event)
            with self._lock:
                if self._mission_title:
                    self._mission_layer = "critic"
            self._send_message(_esc(text))
        elif etype == "life.iteration.continued":
            text = _format_iteration_continued(event)
            with self._lock:
                if self._mission_title:
                    self._mission_layer = "critic"
            self._send_message(_esc(text))
        elif etype == "life.planner.start":
            self.start_mission(
                title=event.get("objective", "规划中…")[:80],
                layer="planner",
            )
        elif etype in ("life.planner.verdict", "life.planner.error"):
            status = "failed" if etype.endswith(".error") else "done"
            self.end_mission(status=status)

    def start_mission(self, *, title: str, layer: str = "engineer") -> None:
        with self._lock:
            self._mission_title = title
            self._mission_layer = layer
            self._mission_start = time.time()
            self._event_count = 0
            self._buf.clear()
        self._request_flush()

    def end_mission(self, *, status: str = "done") -> None:
        with self._lock:
            items = list(self._buf)
            self._buf.clear()
            title = self._mission_title
            layer = self._mission_layer
            start = self._mission_start
            event_count = self._event_count
            self._mission_title = ""
            self._mission_layer = ""
            self._mission_start = 0.0
            self._event_count = 0
        if not title:
            return
        self._send_progress_cards(items, title=title, layer=layer, start=start)
        self._send_message(self._format_mission_status_card(
            title=title,
            layer=layer,
            start=start,
            status=status,
            event_count=event_count,
        ))

    def _format_round_start_card_locked(self, round_label: str, raw_text: str) -> str:
        suffix = " · resume" if "resuming" in raw_text else ""
        lines = [
            f"👷 <b>L1 工程师 Round {_esc(round_label)} 开始{suffix}</b>",
            f"📌 {_esc(self._mission_title[:100])}",
        ]
        return "\n".join(lines)

    def _format_round_completed_card_locked(
        self,
        event: dict[str, Any],
        round_label: str,
        failed: bool,
    ) -> str:
        status = "失败" if failed else "完成"
        icon = "❌" if failed else "✅"
        lines = [
            f"{icon} <b>L1 工程师 Round {_esc(round_label)} {status}</b>",
        ]
        fatal_error = str(event.get("fatal_error") or "").strip()
        if fatal_error:
            lines.append(f"error：{_esc(_truncate_display(fatal_error, 220))}")
        return "\n".join(lines)

    def _format_review_started_card_locked(self, round_label: str) -> str:
        return f"👨‍🏫 <b>L2 审查 Round {_esc(round_label)} 开始</b>"

    def _format_review_card_locked(self, event: dict[str, Any]) -> str:
        round_label = _round_progress_label(_event_round_number(event), _event_round_max(event))
        status = str(event.get("status") or "?")
        lines = [
            f"{_l2_icon(status)} <b>L2 审查 Round {_esc(round_label)}：{_esc(status)}</b>",
        ]
        confidence = _format_confidence(event.get("confidence"))
        if confidence:
            lines.append(f"confidence：{_esc(confidence)}")
        reason = str(event.get("reason") or "").strip()
        if reason:
            lines.append(f"reason：{_esc(_truncate_display(reason, 220))}")
        next_action = str(event.get("next_action") or "").strip()
        if next_action:
            lines.append(f"next：{_esc(_truncate_display(next_action, 220))}")
        return "\n".join(lines)

    def _append_progress_locked(self, event: dict[str, Any]) -> None:
        message_id = event.get("message_id")
        if event.get("replace") and message_id:
            for idx, old in enumerate(self._buf):
                if (
                    old.get("message_id") == message_id
                    and old.get("kind") == event.get("kind")
                ):
                    self._buf[idx] = event
                    return
        self._buf.append(event)

    # -- background thread ------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            signalled = self._poke.wait(timeout=self.FLUSH_INTERVAL)
            self._poke.clear()
            if self._stop.is_set():
                break
            if signalled:
                elapsed = time.monotonic() - self._last_flush_monotonic
                if elapsed < self.MIN_FLUSH_INTERVAL:
                    self._stop.wait(timeout=self.MIN_FLUSH_INTERVAL - elapsed)
                    if self._stop.is_set():
                        break
            try:
                self._flush()
            except Exception:  # noqa: BLE001
                log.debug("tg-stream flush error", exc_info=True)

    def _request_flush(self) -> None:
        if self._enabled:
            self._poke.set()

    def _flush(self) -> None:
        with self._lock:
            if not self._buf or not self._mission_title:
                return
            items = list(self._buf)
            self._buf.clear()
            title = self._mission_title
            layer = self._mission_layer
            start = self._mission_start

        self._send_progress_cards(items, title=title, layer=layer, start=start)
        self._last_flush_monotonic = time.monotonic()

    def _send_progress_cards(
        self,
        items: list[dict[str, Any]],
        *,
        title: str,
        layer: str,
        start: float,
    ) -> None:
        actions = _summarize_progress(items)
        for action in actions:
            self._send_message(self._format_progress_card(
                action,
                title=title,
                layer=layer,
                start=start,
            ))

    def _format_progress_card(
        self,
        action: str,
        *,
        title: str,
        layer: str,
        start: float,
    ) -> str:
        elapsed = time.time() - start if start else 0
        mins, secs = divmod(int(elapsed), 60)
        layer_label = _LAYER_LABELS.get(layer, layer)
        lines = [f"🧾 <b>实时动作</b> · {mins}m{secs:02d}s"]
        if layer_label:
            lines.append(f"当前层级：{layer_label}")
        lines.append(f"📌 {_esc(title[:80])}")
        lines.append("")
        lines.append(_esc(action))
        body = "\n".join(lines)
        if len(body) > self.MAX_MSG_LEN:
            body = body[:self.MAX_MSG_LEN] + "\n…"
        return body

    def _format_mission_status_card(
        self,
        *,
        title: str,
        layer: str,
        start: float,
        status: str,
        event_count: int,
    ) -> str:
        elapsed = time.time() - start if start else 0
        mins, secs = divmod(int(elapsed), 60)
        layer_label = _LAYER_LABELS.get(layer, layer)
        if status == "failed":
            header = f"❌ <b>任务已失败</b> · {mins}m{secs:02d}s"
        else:
            header = f"✅ <b>任务已完成</b> · {mins}m{secs:02d}s"
        lines = [header]
        if layer_label:
            lines.append(f"结束层级：{layer_label}")
        lines.append(f"📌 {_esc(title[:80])}")
        if event_count:
            lines.append(f"已分散发送 {event_count} 条实时进展")
        lines.append("完整日志：argus-skill --follow")
        return "\n".join(lines)

    # -- Telegram API helpers ---------------------------------------------

    def _send_message(self, text: str) -> int | None:
        try:
            import urllib.request
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            body = json.dumps({
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "disable_notification": True,
            }, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data.get("result", {}).get("message_id")
        except Exception:  # noqa: BLE001
            log.debug("tg-stream send failed", exc_info=True)
            return None

    def _edit_message(self, msg_id: int, text: str) -> bool:
        try:
            import urllib.request
            url = f"https://api.telegram.org/bot{self._token}/editMessageText"
            body = json.dumps({
                "chat_id": self._chat_id,
                "message_id": msg_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status < 400
        except Exception:  # noqa: BLE001
            log.debug("tg-stream edit failed", exc_info=True)
            return False

    def _delete_message(self, msg_id: int) -> None:
        try:
            import urllib.request
            url = f"https://api.telegram.org/bot{self._token}/deleteMessage"
            body = json.dumps({
                "chat_id": self._chat_id,
                "message_id": msg_id,
            }).encode("utf-8")
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "dispatch_journal_entry",
    "DEFAULT_NOTIFY_KINDS",
    "TelegramStreamReporter",
    "telegram_enabled",
]
