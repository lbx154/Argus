"""Planner context and reviewer-feedback rendering mixin."""

from __future__ import annotations

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


class PlannerRenderingMixin:
    def _item_iteration_cycles(self) -> int:
        """Default iteration cycles for planner-generated tasks."""
        try:
            return max(1, int(self.config.planner_task_iteration_max_cycles))
        except (TypeError, ValueError):
            return 6

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
        return "\n".join(lines) or "(empty)"

__all__ = ["PlannerRenderingMixin"]
