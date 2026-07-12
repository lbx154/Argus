"""Mission completion rendering and REPL continuation state."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _no_executor_notice(item_id: str, theme: Any) -> str:
    """Honest message when a task is queued but no daemon will execute it.

    Replaces the old "queued — daemon executing" line (which lied when the
    auto-spawn had failed) AND avoids the 600s tail-wait freeze. The task is
    persisted, so it runs the moment an executor starts.
    """
    head = f"queued {item_id} — but NO daemon is running here, so it will NOT execute yet."
    body = (
        "   in this cockpit:  /daemon start   ·   diagnose:  /doctor\n"
        "   from another shell:  argus-skill --daemon\n"
        "   your task is saved and runs the moment a daemon starts."
    )
    if theme is not None:
        head_lines = theme.wrap_after(head, first_indent=2, hang_indent=2)
        head_out = "\u26a0 " + head_lines[0]
        if len(head_lines) > 1:
            head_out += "\n" + "\n".join(head_lines[1:])
        return theme.yellow(head_out) + "\n" + theme.gray(body)
    return f"\u26a0 {head}\n{body}"


def _format_completion(
    final: dict[str, Any],
    item_id: str,
    life_dir: Path | str,
    *,
    workdir: Path | str | None = None,
) -> list[str]:
    """Render the multi-line mission-completion footer.

    The bare ``status=`` line was the engineer's last word; the operator wants
    the **reviewer's** conclusion (the sole done-ness authority) and *where the
    result lives*. Lines:

      ``✅ <id> done · status=<s> · <n>r · cost=$<c>``
      ``   reviewer <verdict> (conf <c>): <reason>``   (only if a verdict exists)
      ``   record: <life_dir>``                         (journal/checkpoint/events)
      ``   workdir: <cwd>``                             (where code artifacts land)
    """
    status = str(final.get("status") or "?")
    head = f"✅ {item_id} done · status={status}"
    rounds = final.get("rounds")
    if isinstance(rounds, int) and rounds:
        head += f" · {rounds}r"
    pricing_status = str(final.get("pricing_status") or "")
    raw_cost = final.get("cost_usd")
    try:
        cost = float(raw_cost) if raw_cost is not None else None
    except (TypeError, ValueError):
        cost = None
    if cost is not None:
        suffix = "+" if pricing_status in {"partial", "unpriced"} else ""
        head += f" · cost=${cost:.4f}{suffix}"
    elif pricing_status in {"partial", "unpriced"}:
        head += f" · cost={pricing_status}"
    lines = [head]

    review = final.get("_last_review") or {}
    reason = str(review.get("reason") or "").strip()
    if reason:
        rstatus = str(review.get("status") or "").strip()
        conf = review.get("confidence")
        cpart = f" (conf {conf:.2f})" if isinstance(conf, (int, float)) else ""
        lead = "reviewer" + (f" {rstatus}" if rstatus else "") + cpart
        lines.append(f"   {lead}: {reason}")

    lines.append(f"   record: {life_dir}")
    wd = Path(workdir) if workdir is not None else Path.cwd()
    if str(wd) != str(life_dir):
        lines.append(f"   workdir: {wd}")
    return lines


def _record_mission_outcome(
    chat_state: dict[str, Any],
    completed_event: dict[str, Any],
) -> None:
    """Update REPL session stats from a tailed ``life.mission.completed`` event.

    The REPL no longer drives the supervisor, so timing / count come from the
    event the daemon wrote rather than from an in-process return value.
    """
    chat_state["mission_count"] = chat_state.get("mission_count", 0) + 1
    cost = completed_event.get("cost_usd")
    try:
        chat_state["last_cost_usd"] = float(cost) if cost is not None else None
    except (TypeError, ValueError):
        chat_state["last_cost_usd"] = None
    # Remember a blocked verdict so the next free-text reply continues THIS item
    # (answer injected) instead of being triaged as a brand-new objective. The
    # operator question is surfaced by ``_surface_blocked_question``. Cleared on
    # any non-blocked outcome.
    review = completed_event.get("_last_review") or {}
    if str(review.get("status") or completed_event.get("status") or "") == "blocked":
        chat_state["blocked_item_id"] = completed_event.get("item_id")
        chat_state["blocked_question"] = (
            str(review.get("operator_question") or "").strip()
            or str(review.get("reason") or "").strip()
        )
    else:
        chat_state.pop("blocked_item_id", None)
        chat_state.pop("blocked_question", None)


def _surface_blocked_question(chat_state: dict[str, Any], theme: Any) -> None:
    """Print the operator question for a just-blocked mission, if any. The
    operator answers by typing a normal reply — no slash command needed."""
    q = str(chat_state.get("blocked_question") or "").strip()
    if not q:
        return
    line = f"❓ Needs your call: {q} (reply to continue this task)"
    print(theme.yellow(line) if theme is not None else line, flush=True)


def _format_elapsed(seconds: float) -> str:
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins, secs = divmod(int(seconds), 60)
    if mins < 60:
        return f"{mins}m{secs:02d}s"
    hours, mins = divmod(mins, 60)
    return f"{hours}h{mins:02d}m{secs:02d}s"

__all__ = [
    "_format_completion",
    "_format_elapsed",
    "_no_executor_notice",
    "_record_mission_outcome",
    "_surface_blocked_question",
]
