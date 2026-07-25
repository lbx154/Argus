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
# Terminal outcomes worth counting for the campaign tally below.
_PLANNER_TALLY_KINDS = (
    "mission_complete",
    "mission_failed",
    "mission_replan_requested",
)


class PlannerRenderingMixin:
    def _item_iteration_cycles(self) -> int:
        """Default iteration cycles for planner-generated tasks."""
        try:
            return max(1, int(self.config.planner_task_iteration_max_cycles))
        except (TypeError, ValueError):
            return 6

    def _render_campaign_tally(self) -> str:
        """Whole-campaign terminal-outcome counts, as facts and nothing else.

        The detailed history window below is only ``_PLANNER_HISTORY_COUNT``
        entries, which is the right size for "what just happened" but makes the
        campaign's shape invisible: a project can close dozens of missions and
        the Planner still only ever sees the last few. It is then asked whether
        the project is done or the strategy needs replacing while having no way
        to notice that nothing has been replanned in a hundred cycles.

        This reports counts only. Whether that pattern means "keep going",
        "change approach", or "this is finished" is the Planner's call — the
        harness must not pre-chew it into a recommendation.
        """
        try:
            entries = list(self.memory.journal.tail(4096))
        except Exception:  # noqa: BLE001 — planner context is best-effort
            return ""
        counts = {kind: 0 for kind in _PLANNER_TALLY_KINDS}
        total = 0
        since_replan: int | None = None
        for entry in entries:
            kind = getattr(entry, "kind", "")
            if kind not in counts:
                continue
            counts[kind] += 1
            total += 1
            since_replan = (
                0 if kind == "mission_replan_requested"
                else (since_replan + 1 if since_replan is not None else None)
            )
        if total == 0:
            return ""
        parts = [f"{kind.removeprefix('mission_')}={counts[kind]}" for kind in _PLANNER_TALLY_KINDS]
        line = f"Campaign totals ({total} terminal missions): " + ", ".join(parts)
        if counts["mission_replan_requested"] == 0:
            line += "; no mission has ever requested a replacement plan"
        elif since_replan:
            line += f"; {since_replan} terminal missions since the last replan"
        return line

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
        tally = self._render_campaign_tally()
        return f"{tally}\n{body}" if tally else body

__all__ = ["PlannerRenderingMixin"]
