"""Planner runtime gates, context, and failure-quarantine helpers."""

from __future__ import annotations

import logging
from typing import Any

from ._helpers import (
    _entry_task_signature,
    _is_recent_no_progress_failure,
)
from ._subagent_family_failures import (
    SubagentFamilyFailure,
    recent_subagent_family_failures,
)

log = logging.getLogger(__name__)

_PLANNER_RECENT_HISTORY_WINDOW = 20


class PlannerOrchestrationMixin:
    def _planner_cycle_gate_reason(self) -> str:
        gate = self.config.planner_cycle_gate
        if gate is None:
            return ""
        try:
            reason = gate()
        except Exception:  # noqa: BLE001
            log.exception("planner cycle gate raised; continuing with planner")
            return ""
        return str(reason or "").strip()

    def _planner_runtime_context(self) -> str:
        provider = self.config.planner_runtime_context_provider
        if provider is None:
            return ""
        try:
            context = provider()
        except Exception:  # noqa: BLE001
            log.exception("planner runtime context provider raised; continuing")
            return ""
        return str(context or "").strip()

    def _planner_project_context(self) -> str:
        """Return cheap project-state context that keeps planner work grounded."""
        return self._planner_runtime_context()

    def _planner_runtime_with_idle_note(self) -> str:
        """Prefix repeated idle cycles with a current-reality check."""
        base = self._planner_project_context()
        contract_note = self._planner_waiting_contract_runtime_note()
        n = int(getattr(self, "_consecutive_idle_planner_cycles", 0))
        if n < 2:
            return "\n\n".join(part for part in (contract_note, base) if part)
        note = (
            "CURRENT-REALITY CHECK (read before trusting the journal below): you "
            f"have idled {n} consecutive cycle(s) concluding `waiting=true` on the "
            "same blocker. Your journal may be STALE — the external dependency may "
            "already have cleared. Before concluding `waiting` again, compare CURRENT "
            "evidence to your persisted recheck condition. Reuse the same contract "
            "token while it is unchanged; the harness permits at most one probe for "
            "each Planner-authored fingerprint/token pair."
        )
        return "\n\n".join(part for part in (contract_note, note, base) if part)

    def _recent_no_progress_failures(self) -> dict[tuple[str, str], Any]:
        """Return recent failed task signatures quarantined from replanning."""
        try:
            recent_entries = self.memory.journal.tail(_PLANNER_RECENT_HISTORY_WINDOW)
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to read recent journal for planner")
            return {}
        matches: dict[tuple[str, str], Any] = {}
        for entry in reversed(recent_entries):
            if not _is_recent_no_progress_failure(entry):
                continue
            signature = _entry_task_signature(entry)
            if signature is None or signature in matches:
                continue
            matches[signature] = entry
        return matches

    def _recent_subagent_family_failures(self) -> dict[str, SubagentFamilyFailure]:
        """Return subagent-job families stuck in an unresolved failure streak."""
        try:
            streak_limit = int(
                getattr(self.config, "subagent_family_failure_streak_limit", 3)
            )
        except (TypeError, ValueError):
            streak_limit = 3
        try:
            window_hours = float(
                getattr(self.config, "subagent_family_failure_window_hours", 72.0)
            )
        except (TypeError, ValueError):
            window_hours = 72.0
        if streak_limit <= 0:
            return {}
        try:
            return recent_subagent_family_failures(
                self._project_workdir(),
                window_seconds=max(0.0, window_hours) * 3600.0,
                min_streak=streak_limit,
            )
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to read subagent registry for planner")
            return {}

    @staticmethod
    def _task_mentions_family(task: Any, family: str) -> bool:
        if not family:
            return False
        haystack = " ".join((task.title, task.objective, task.evidence)).casefold()
        needle = family.casefold()
        if needle in haystack:
            return True
        return needle.replace("-", "_") in haystack.replace("-", "_")

    @staticmethod
    def _stuck_subagent_families_note(
        family_failures: dict[str, SubagentFamilyFailure],
    ) -> str:
        if not family_failures:
            return ""
        lines = [
            "STUCK EXPERIMENT FAMILIES (facts, not a directive on what to do "
            "instead): the following subagent job families have failed "
            "repeatedly, back-to-back, with no successful completion in "
            "between. A bare resubmission with an unchanged strategy will be "
            "AUTOMATICALLY SKIPPED by the supervisor (it will not reach the "
            "engineer) — propose either a materially different approach "
            "(root-cause fix, reduced scope, alternate method) or an explicit "
            "operator-escalation task instead.",
        ]
        for failure in sorted(family_failures.values(), key=lambda f: -f.streak):
            reason = (
                f" (last failure: {failure.last_reason})"
                if failure.last_reason
                else ""
            )
            lines.append(
                f"  - {failure.family}: {failure.streak} consecutive "
                f"{failure.last_state} attempt(s), most recently "
                f"{failure.last_task_id!r}{reason}"
            )
        return "\n".join(lines)

    def _handle_planner_restart(self, reason: str) -> bool:
        handler = self.config.planner_restart_handler
        if handler is None:
            return False
        try:
            return bool(handler(reason))
        except Exception:  # noqa: BLE001
            log.exception("planner restart handler raised; continuing")
            return False

    def _post_mission_hook(self, outcome: dict[str, Any]) -> str:
        hook = self.config.post_mission_hook
        if hook is None:
            return ""
        try:
            return str(hook(outcome) or "").strip()
        except Exception:  # noqa: BLE001
            log.exception("post mission hook raised; continuing")
            return ""


__all__ = ["PlannerOrchestrationMixin"]
