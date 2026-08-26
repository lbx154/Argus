"""Planner context and reviewer-feedback rendering mixin."""

from __future__ import annotations

from collections import Counter
from typing import Any

_PLANNER_HISTORY_KINDS = frozenset({
    "budget_pause",
    "mission_complete",
    "mission_failed",
    "mission_replan_requested",
    "provider_pause",
    "research_pause",
})
_PLANNER_HISTORY_COUNT = 3
_PLANNER_HISTORY_ENTRY_CHARS = 1_800
# Terminal outcomes worth counting for the campaign tally below.
_PLANNER_TALLY_KINDS = (
    "mission_complete",
    "mission_failed",
    "mission_replan_requested",
)


def _payload(entry: Any) -> dict[str, Any]:
    """Return the settled mission event carried by a journal entry."""
    extra = getattr(entry, "extra", None)
    return extra if isinstance(extra, dict) else {}


def _forward_progress(entry: Any) -> bool | None:
    """Return the Reviewer's own objective-level verdict, if it recorded one.

    A mission can close as ``mission_complete`` because the round was correct
    while the Reviewer still judged that the operator's objective did not move.
    That distinction is the whole point of the field, and it is invisible in the
    kind counts.
    """
    report = _payload(entry).get("planner_report")
    value = report.get("forward_progress") if isinstance(report, dict) else None
    return value if isinstance(value, bool) else None


def _amount(value: Any) -> float:
    """Coerce one recorded cost/duration to a non-negative float."""
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _missions_since_replan(missions: list[Any]) -> int:
    for distance, entry in enumerate(reversed(missions)):
        if getattr(entry, "kind", "") == "mission_replan_requested":
            return distance
    return 0


def _cumulative_price(missions: list[Any]) -> str:
    """Render what the campaign has already spent, or ``""`` when unmeasured."""
    dollars = sum(_amount(getattr(entry, "cost_usd", 0.0)) for entry in missions)
    seconds = sum(_amount(_payload(entry).get("elapsed_seconds")) for entry in missions)
    measured = []
    if dollars >= 0.01:
        measured.append(f"${dollars:,.2f}")
    if seconds >= 360:
        measured.append(f"{seconds / 3600:.1f}h")
    return "cumulative " + " over ".join(measured) if measured else ""


class PlannerRenderingMixin:
    def _render_research_plan_for_planner(self) -> str:
        """Return the fail-soft, bounded living research plan projection."""
        from ..research_plan import render_research_plan_for_planner

        try:
            return render_research_plan_for_planner(self.memory.root)
        except Exception:  # noqa: BLE001 - planner state must never stop a cycle
            return "(no plan yet — create RESEARCH_PLAN.md in this planning cycle)"

    def _apply_research_plan_update(self, raw_text: str) -> bool:
        """Apply an optional full ``PLAN_UPDATE`` block from Planner output."""
        from ...core.role_reply import read_block
        from ...planner.planner import _GLOBAL_KEY_VALUE_KEYS
        from ..research_plan import replace_research_plan

        keys = (*_GLOBAL_KEY_VALUE_KEYS, "PLAN_UPDATE", "TASK_KEY")
        update = read_block(raw_text, "PLAN_UPDATE", keys).strip()
        if not update or not replace_research_plan(self.memory.root, update):
            return False
        # ``user.note`` is the canonical free-form journal event. The file is
        # already durable if event delivery happens to fail.
        self._emit({
            "type": "user.note",
            "title": "research plan updated",
            "text": "research plan updated",
            "tags": ["planner", "research_plan"],
        })
        return True

    def _item_iteration_cycles(self) -> int:
        """Default iteration cycles for planner-generated tasks."""
        try:
            return max(1, int(self.config.planner_task_iteration_max_cycles))
        except (TypeError, ValueError):
            return 6

    def _render_campaign_tally(self) -> str:
        """Whole-campaign terminal facts, as facts and nothing else.

        The detailed history window below is only ``_PLANNER_HISTORY_COUNT``
        entries, which is the right size for "what just happened" but makes the
        campaign's shape invisible: a project can close dozens of missions and
        the Planner still only ever sees the last few. It is then asked whether
        the project is done or the strategy needs replacing while having no way
        to notice that nothing has been replanned in a hundred cycles, that the
        Reviewer has been reporting a stalled objective throughout, or what the
        attempt has already cost.

        This reports counts only. Whether that pattern means "keep going",
        "change approach", or "this is finished" is the Planner's call — the
        harness must not pre-chew it into a recommendation.
        """
        try:
            missions = [
                entry
                for entry in self.memory.journal.tail(4096)
                if getattr(entry, "kind", "") in _PLANNER_TALLY_KINDS
            ]
        except Exception:  # noqa: BLE001 — planner context is best-effort
            return ""
        if not missions:
            return ""
        counts = Counter(getattr(entry, "kind", "") for entry in missions)
        facts = [
            f"Campaign totals ({len(missions)} terminal missions): "
            + ", ".join(
                f"{kind.removeprefix('mission_')}={counts[kind]}"
                for kind in _PLANNER_TALLY_KINDS
            )
        ]
        judged = [
            verdict
            for entry in missions
            if (verdict := _forward_progress(entry)) is not None
        ]
        if flat := judged.count(False):
            facts.append(
                f"{flat} of {len(judged)} reviewed missions reported no "
                "objective-level progress"
            )
        if not counts["mission_replan_requested"]:
            facts.append("no mission has ever requested a replacement plan")
        elif distance := _missions_since_replan(missions):
            facts.append(f"{distance} terminal missions since the last replan")
        if price := _cumulative_price(missions):
            facts.append(price)
        return "; ".join(facts)

    def _render_journal_for_planner(self) -> str:
        """Render a bounded recency window of terminal mission evidence."""
        try:
            entries = [
                entry
                for entry in self.memory.journal.tail(64)
                if entry.kind in _PLANNER_HISTORY_KINDS
            ][-_PLANNER_HISTORY_COUNT:]
        except Exception:  # noqa: BLE001
            return ""
        lines: list[str] = []
        for e in entries:
            from datetime import datetime
            ts = datetime.fromtimestamp(e.ts).strftime("%m-%d %H:%M")
            line = f"- [{ts}] {e.kind}: {e.title} — {e.summary}"
            extra = getattr(e, "extra", {}) or {}
            if isinstance(extra, dict):
                if e.kind in (
                    "mission_complete",
                    "mission_failed",
                    "mission_replan_requested",
                ):
                    context_packet = str(extra.get("context_packet") or "").strip()
                    if context_packet:
                        line += (
                            "\n    sealed_context_packet: "
                            + context_packet[:600]
                        )
            if len(line) > _PLANNER_HISTORY_ENTRY_CHARS:
                line = line[: _PLANNER_HISTORY_ENTRY_CHARS - 1].rstrip() + "…"
            lines.append(line)
        body = "\n".join(lines) or "(empty)"
        try:
            failure_context = self.memory.render_failure_experience_context(
                self.config.continuous_objective,
            ).strip()
        except (AttributeError, OSError, TypeError, ValueError):
            failure_context = ""
        if failure_context:
            body += "\n\n" + failure_context
        tally = self._render_campaign_tally()
        return f"{tally}\n{body}" if tally else body

__all__ = ["PlannerRenderingMixin"]
