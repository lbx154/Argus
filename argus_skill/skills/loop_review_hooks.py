"""Reviewed-round hooks + post-completion skill-maintenance phase for
``SkillLoop.run``.

Covers the three callbacks handed to ``SupervisedEngineer.run``:
pre-review wiki-hook priming (``_prepare_review_context``), per-reviewed-
round context-packet/wiki capture (``_capture_reviewed_round``), and the
same-session Engineer skill create/update continuation invoked after a
self-approved completion (``_maintain_skill_with_engineer``). Extracted
verbatim from the historical nested closures in ``SkillLoop.run``.
"""
from __future__ import annotations

import logging

from ..core.event_catalog import EventType
from ..core.models import RoundRecord, RunnerOptions
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.secret_guard import known_secret_values, redact_secrets_text
from ..engineer.self_review import (
    EngineerCompletionDecision,
    EngineerSkillMaintenanceOutcome,
)
from .loop_state import MissionContext, SkillSelectionState

log = logging.getLogger(__name__)


class ReviewedRoundHooksMixin:
    """Reviewed-round hook + skill-maintenance phase methods for ``SkillLoop``."""

    def _prepare_review_context(self, mission: MissionContext) -> None:
        if not self.config.wiki_ops_enabled:
            return
        from ..wiki.auto_hooks import run_post_mission_hooks

        run_post_mission_hooks(
            mission.workdir,
            mission_id=mission.run_id,
            success=False,
            emit=self.on_event,
        )

    def _capture_reviewed_round(self, mission: MissionContext, record: RoundRecord) -> None:
        if self.config.context_packet_path:
            try:
                from ..life.context_packet import record_reviewed_handoff

                record_reviewed_handoff(
                    mission_context_path=self.config.context_packet_path,
                    round_index=record.round_index,
                    engineer_summary=record.engineer_message,
                    review=record.review,
                    checkpoint_path=self.config.checkpoint_path,
                )
            except Exception:  # noqa: BLE001 - handoff persistence is fail-soft
                log.exception("failed to persist reviewed context packet")
        if not self.config.wiki_ops_enabled:
            return
        from ..wiki.lifecycle import capture_reviewed_round as _capture

        _capture(
            record=record,
            workdir=mission.workdir,
            task=mission.skill_task,
            mission_id=mission.run_id,
            on_event=self.on_event,
            context_packet_path=self.config.context_packet_path,
            checkpoint_path=self.config.checkpoint_path,
        )

    def _maintain_skill_with_engineer(
        self,
        mission: MissionContext,
        state: SkillSelectionState,
        decision: EngineerCompletionDecision,
        thread_id: str | None,
        engineer_summary: str,
    ) -> EngineerSkillMaintenanceOutcome:
        action = decision.skill_action
        if action not in {"create", "update"}:
            return EngineerSkillMaintenanceOutcome()
        if not thread_id:
            return EngineerSkillMaintenanceOutcome(
                attempted=False,
                success=False,
                summary="same-session continuation unavailable: no thread id",
            )
        target_name = decision.skill_name.strip()
        action_instruction = (
            "Create one new reusable Engineer skill."
            if action == "create"
            else (
                "Return a complete replacement for the existing Engineer "
                f"skill named `{target_name}`; preserve that exact title."
            )
        )
        prompt = (
            "Continue the SAME Engineer session. The project task is already "
            "complete and self-verified. Do not change project deliverables, "
            "rerun the task, invoke a Reviewer, or launch subagents. Perform "
            "only the requested reusable skill maintenance.\n\n"
            f"Action: {action_instruction}\n"
            "Decide from the completed work below whether a durable reusable "
            "mechanism actually exists; output `NONE` if it does not.\n\n"
            "Generalize away mission IDs, local absolute paths, exact issue "
            "text, and one-off constants. Return exactly one Markdown skill "
            "with these sections: `# <title>`, `## Description`, "
            "`## Category`, `## When to use`, `## When NOT to use`, "
            "`## How to solve`, and `## Pitfalls`. If the trajectory does "
            "not support a defensible reusable skill after all, output "
            "exactly `NONE`.\n\n"
            "For context, the completed Engineer summary was:\n"
            + engineer_summary[-8000:]
        )
        self._emit({
            "type": EventType.ENGINEER_SKILL_MAINTENANCE_STARTED,
            "action": action,
            "name": target_name,
            "session_id": thread_id,
            "text": (
                "resuming Engineer session for skill "
                f"{action}{f' `{target_name}`' if target_name else ''}"
            ),
        })
        backend_name = str(
            getattr(self.engineer_runner, "_backend_name", "") or ""
        ).strip().lower()
        extra_args = list(self.config.extra_args or [])
        sandbox_mode: str | None = "read-only"
        if backend_name == "copilot":
            sandbox_mode = None
            extra_args.extend([
                "--no-custom-instructions",
                "--disable-builtin-mcps",
                "--available-tools=",
            ])
        try:
            result = gateway_run_exec(
                self.engineer_runner,
                prompt=prompt,
                options=RunnerOptions(
                    model=self.config.engineer_model,
                    reasoning_effort=(
                        self.config.skill_maintenance_reasoning_effort
                    ),
                    extra_args=extra_args or None,
                    full_auto=False,
                    dangerous_yolo=False,
                    sandbox_mode=sandbox_mode,
                    skip_git_repo_check=True,
                    working_dir=str(mission.workdir),
                ),
                run_label="engineer-skill-maintenance",
                resume_thread_id=thread_id,
            )
        except Exception as exc:  # noqa: BLE001
            self._emit({
                "type": EventType.ENGINEER_SKILL_MAINTENANCE_COMPLETED,
                "action": action,
                "name": target_name,
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_output_tokens": 0,
                "premium_requests": 0.0,
                "usage_scope": "delta",
                "text": "Engineer skill maintenance call failed",
            })
            return EngineerSkillMaintenanceOutcome(
                attempted=True,
                success=False,
                summary=f"failed: {type(exc).__name__}: {exc}",
                thread_id=thread_id,
            )

        raw_content = str(getattr(result, "last_agent_message", "") or "").strip()
        content = redact_secrets_text(
            raw_content,
            known_values=known_secret_values(),
        )
        counts = {"created": 0, "updated": 0, "archived": 0, "rejected": 0}
        error = str(getattr(result, "fatal_error", "") or "").strip()
        call_ok = int(getattr(result, "exit_code", 0) or 0) == 0 and not error
        if call_ok and content and not content.upper().startswith("NONE"):
            op = {
                "op": action,
                "content": content,
                "why": "Engineer requested post-task skill maintenance",
            }
            if action == "update":
                op["name"] = target_name
            counts = self.skill_router.apply_ops(
                [op],
                task=mission.skill_task,
                on_event=self._emit,
            )
            if action == "create" and counts["created"]:
                from .skill_prompts import Prompts

                created_name, _, _, _ = Prompts.parse_skill_output(content)
                state.skill_name = created_name
                state.skill_distilled = True
        success = bool(counts["created"] or counts["updated"])
        if call_ok and content.upper().startswith("NONE"):
            summary = "Engineer found no defensible reusable skill"
        elif success:
            summary = (
                f"{action} applied"
                + (f" for `{target_name}`" if target_name else "")
            )
        elif error:
            summary = f"failed: {error}"
        else:
            summary = "skill candidate rejected or empty"
        self._emit({
            "type": EventType.ENGINEER_SKILL_MAINTENANCE_COMPLETED,
            "action": action,
            "name": target_name,
            "success": success,
            "counts": counts,
            "error": error,
            "session_id": getattr(result, "thread_id", None) or thread_id,
            "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
            "cached_input_tokens": int(
                getattr(result, "cached_input_tokens", 0) or 0
            ),
            "output_tokens": int(getattr(result, "output_tokens", 0) or 0),
            "reasoning_output_tokens": int(
                getattr(result, "reasoning_output_tokens", 0) or 0
            ),
            "premium_requests": float(
                getattr(result, "premium_requests", 0.0) or 0.0
            ),
            "usage_scope": "delta",
            "text": summary,
        })
        return EngineerSkillMaintenanceOutcome(
            attempted=True,
            success=success,
            summary=summary,
            thread_id=getattr(result, "thread_id", None) or thread_id,
        )
