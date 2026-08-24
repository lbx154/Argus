"""Planner runtime gates, context, and failure-quarantine helpers."""

from __future__ import annotations

import json
import logging
import subprocess
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
    def _live_subagent_id_line(self) -> str:
        """Name the live subagent work_ids, or say nothing.

        Empty while nothing is running, so a quiet campaign pays no prompt for
        it, and empty on any probe failure because the digest is advisory.
        """
        try:
            ids = sorted(
                {
                    work_id
                    for job in self._waitable_subagent_jobs()
                    if (work_id := str(getattr(job, "work_id", "") or ""))
                }
            )
        except Exception:  # noqa: BLE001 - the digest is advisory
            return ""
        if not ids:
            return ""
        return (
            "- live_subagent_work_ids (copy one exactly into any subagent "
            "event wait): " + ", ".join(ids)
        )

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

    def _planner_runtime_with_idle_note(self) -> str:
        """Prefix repeated idle cycles with a current-reality check."""
        base = self._planner_current_reality_note()
        resolution_note = self._planner_wait_resolution_runtime_note()
        contract_note = self._planner_waiting_contract_runtime_note()
        manager_feedback = self._manager_planner_feedback_runtime_note()
        n = int(getattr(self, "_consecutive_idle_planner_cycles", 0))
        if n < 2:
            return "\n\n".join(
                part
                for part in (
                    resolution_note,
                    manager_feedback,
                    contract_note,
                    base,
                )
                if part
            )
        note = (
            "CURRENT-REALITY CHECK (read before trusting the journal below): you "
            f"have idled {n} consecutive cycle(s) concluding `waiting=true` on the "
            "same blocker. Your journal may be STALE — the external dependency may "
            "already have cleared. Before concluding `waiting` again, compare CURRENT "
            "evidence to your persisted recheck condition. Reuse the same contract "
            "token while it is unchanged; the harness permits at most one probe for "
            "each Planner-authored fingerprint/token pair."
        )
        return "\n\n".join(
            part
            for part in (
                resolution_note,
                manager_feedback,
                contract_note,
                note,
                base,
            )
            if part
        )

    def _planner_current_reality_note(self) -> str:
        """Render host-read state so Planner does not rediscover bookkeeping."""
        from ...core.pipeline_state import read_pipeline_state

        artifact_root = self._artifact_root()
        project_root = self._project_workdir()
        try:
            pipeline = read_pipeline_state(artifact_root)
        except (OSError, ValueError):
            pipeline = {}

        stage_rows: list[str] = []
        stages = pipeline.get("stages")
        if isinstance(stages, dict):
            for name, value in list(stages.items())[:12]:
                status = value.get("status") if isinstance(value, dict) else value
                stage_rows.append(f"{name}:{status or 'unknown'}")

        backlog_rows: list[Any] = []
        try:
            backlog_rows = list(self.memory.backlog.all())
        except Exception:  # noqa: BLE001 - digest is advisory
            pass
        # Rendered because the supervisor no longer stops the campaign for an
        # unanswered question. Without the text of what is waiting, the Planner
        # can reword the blocked work and slip past exact-signature dedupe.
        awaiting = [
            f"{getattr(item, 'id', '')}: {str(item.pending_question).strip()}"
            for item in backlog_rows
            if str(getattr(item, "pending_question", "") or "").strip()
        ]
        backlog_counts: dict[str, int] = {}
        for item in backlog_rows:
            status = str(getattr(item, "status", "") or "unknown")
            backlog_counts[status] = backlog_counts.get(status, 0) + 1

        # A subagent event wait is bound by matching the Planner's own words
        # against a live work_id, exactly. The ids were never shown to it, so
        # it wrote what it knew: run-03 named the parent mission, run-01
        # described "the active DARC-DPT monitor/subagent". Both jobs were
        # genuinely running and both waits were thrown away. Rendered only
        # while something is live, so a quiet campaign pays nothing.
        live_subagent_line = self._live_subagent_id_line()

        changed_paths: list[str] = []
        try:
            status_result = subprocess.run(
                ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if status_result.returncode == 0:
                changed_paths = [
                    line[3:].strip()
                    for line in status_result.stdout.splitlines()
                    if len(line) >= 4
                ]
        except (OSError, subprocess.SubprocessError):
            pass

        blockers: list[str] = []
        checkpoint_paths = list(
            dict.fromkeys(
                [
                    project_root / "CHECKPOINT.md",
                    artifact_root / "CHECKPOINT.md",
                ]
            )
        )
        for checkpoint_path in checkpoint_paths:
            try:
                lines = checkpoint_path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            in_blockers = False
            for line in lines:
                if line.startswith("#"):
                    in_blockers = (
                        line.lstrip("# ").strip().casefold()
                        == "open questions / blockers"
                    )
                    continue
                if in_blockers and line.strip():
                    blockers.append(line.strip())
                    if len(blockers) >= 8:
                        break
            if len(blockers) >= 8:
                break

        changed_preview = ", ".join(changed_paths[:12]) or "(clean or unavailable)"
        if len(changed_paths) > 12:
            changed_preview += f", +{len(changed_paths) - 12} more"
        return "\n".join(
            [
                "## Host current-reality digest",
                f"- vertical: {pipeline.get('vertical') or '(unresolved)'}",
                f"- workflow_mode: {pipeline.get('workflow_mode') or '(unset)'}",
                f"- current_stage: {pipeline.get('current_stage') or self._current_pipeline_stage() or '(unset)'}",
                f"- stage_statuses: {', '.join(stage_rows) or '(none)'}",
                f"- backlog_counts: {json.dumps(backlog_counts, sort_keys=True)}",
                *([live_subagent_line] if live_subagent_line else []),
                (
                    "- awaiting_operator_answer: "
                    + "; ".join(awaiting)
                    + " — these stay the operator's to decide. Plan work that "
                    "does not depend on the answer rather than a reworded "
                    "version of the same question."
                    if awaiting
                    else "- awaiting_operator_answer: (none)"
                ),
                f"- git_changed_paths ({len(changed_paths)}): {changed_preview}",
                f"- checkpoint_blockers: {'; '.join(blockers) or '(none declared)'}",
                "The host already read pipeline state, backlog, checkpoint blockers, "
                "and Git status for this digest. Do not spend tools rereading those "
                "sources unless a named contradiction requires exact content.",
            ]
        )

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
