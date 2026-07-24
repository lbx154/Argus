"""Prompt/context assembly phase for ``SkillLoop.run``.

Owns building the per-round Engineer prompt: the static Effective Task
Contract + skill playbook + original-request + current-task scaffold
(``_build_engineer_prompt``), plus draining any live Manager/operator
steering guidance on top of it (``_build_round_prompt``, extracted
verbatim from the historical ``build_prompt`` closure).
"""
from __future__ import annotations

import logging

from ..core.event_catalog import EventType
from .loop_state import MissionContext, SkillSelectionState

log = logging.getLogger(__name__)


class PromptContextMixin:
    """Prompt-assembly phase methods for ``SkillLoop``."""

    def _build_round_prompt(self, mission: MissionContext, state: SkillSelectionState, next_action: str | None, include_static: bool = True) -> str:
        prompt = self._build_engineer_prompt(
            task=mission.task,
            skill_text=state.skill_text,
            next_action=next_action,
            original_request=mission.request_anchor,
            include_static=include_static,
            role_banner=mission.engineer_role_banner,
            matched_skill_name=state.learning_target_name,
            require_post_task_learning=self.config.require_post_task_learning,
            force_post_task_learning=self.config.force_post_task_learning,
            file_read_budget=self.config.engineer_file_read_budget,
            test_run_budget=self.config.engineer_test_run_budget,
        )
        guidance: list[str] = []
        if self.extra_guidance_provider is not None:
            try:
                guidance = [
                    str(item).strip()
                    for item in self.extra_guidance_provider()
                    if str(item).strip()
                ]
            except Exception:  # noqa: BLE001 — steering must fail soft
                log.exception("live Manager guidance provider failed")
        if not guidance:
            return prompt
        self._emit({
            "type": EventType.LIFE_INBOX_DRAINED,
            "count": len(guidance),
            "messages": guidance,
            "source": "engineer_round",
        })
        from ..roles.prompts.engineer import append_live_guidance

        return append_live_guidance(prompt, guidance)

    @staticmethod
    def _build_engineer_prompt(
        *,
        task: str,
        skill_text: str,
        next_action: str | None,
        original_request: str = "",
        include_static: bool = True,
        role_banner: str = "",
        matched_skill_name: str = "",
        require_post_task_learning: bool = False,
        force_post_task_learning: bool = False,
        file_read_budget: int = 12,
        test_run_budget: int = 3,
    ) -> str:
        from ..roles.prompts.engineer import build_mission_prompt

        return build_mission_prompt(
            task=task,
            skill_text=skill_text,
            next_action=next_action,
            original_request=original_request,
            include_static=include_static,
            role_banner=role_banner,
            matched_skill_name=matched_skill_name,
            require_post_task_learning=require_post_task_learning,
            force_post_task_learning=force_post_task_learning,
            file_read_budget=file_read_budget,
            test_run_budget=test_run_budget,
        )
