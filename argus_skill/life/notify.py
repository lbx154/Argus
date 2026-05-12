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
* ``ARGUS_SKILL_TELEGRAM_BOT_TOKEN`` + ``ARGUS_SKILL_TELEGRAM_CHAT_ID``:
  sends formatted messages to a Telegram chat via the Bot API.

All are best-effort: any failure is logged at WARNING and dropped on
the floor so the supervisor never crashes because of a flaky pager.
"""
from __future__ import annotations

import json
import logging
import os
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
    "planner_done",
    "phase_change",
})


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
    "planner_done":      ("🏁", "项目完成"),
    "phase_change":      ("🔄", "层级切换"),
}

_LAYER_LABELS: dict[str, str] = {
    "engineer": "👷 工程师 (L1)",
    "reviewer": "👨‍🏫 审查员 (L2)",
    "critic":   "👔 评审员 (L3)",
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
    elif kind == "planner_cycle":
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


__all__ = ["dispatch_journal_entry", "DEFAULT_NOTIFY_KINDS"]
