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
from .loop_state import MissionContext, SkillLibraryState

log = logging.getLogger(__name__)


def _resolve_project_skill_dir(skill_store: object) -> str | None:
    """Return the Engineer-owned project Skill directory."""
    from .role_memory import project_role_skill_dir

    value = project_role_skill_dir(skill_store, "engineer")
    return str(value) if value is not None else None


class PromptContextMixin:
    """Prompt-assembly phase methods for ``SkillLoop``."""

    def _build_round_prompt(self, mission: MissionContext, state: SkillLibraryState, next_action: str | None, include_static: bool = True) -> str:
        compact_team = (
            str(getattr(self.config, "workflow_mode", "") or "") == "direct"
        )
        task = mission.task
        context_packet_path = str(
            getattr(self.config, "context_packet_path", "") or ""
        )
        if compact_team and context_packet_path:
            from ..life.context_packet import render_mission_contract

            task = (
                render_mission_contract(context_packet_path)
                or task
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
        if guidance:
            self._emit({
                "type": EventType.LIFE_INBOX_DRAINED,
                "count": len(guidance),
                "messages": guidance,
                "source": "engineer_round",
            })
        prompt = self._build_engineer_prompt(
            task=task,
            skill_text=state.skill_text,
            next_action=next_action,
            original_request=mission.request_anchor,
            include_static=include_static,
            role_banner=mission.engineer_role_banner,
            require_post_task_learning=self.config.require_post_task_learning,
            project_root=mission.workdir,
            project_skill_dir=_resolve_project_skill_dir(self.skill_store),
            compact_team=compact_team,
            operator_context="\n\n".join(guidance),
        )
        return prompt

    @staticmethod
    def _build_engineer_prompt(
        *,
        task: str,
        skill_text: str,
        next_action: str | None,
        original_request: str = "",
        include_static: bool = True,
        role_banner: str = "",
        require_post_task_learning: bool = False,
        project_root=None,
        project_skill_dir: str | None = None,
        compact_team: bool = False,
        operator_context: str = "",
    ) -> str:
        from ..roles.prompts.engineer import build_mission_prompt

        return build_mission_prompt(
            task=task,
            skill_text=skill_text,
            next_action=next_action,
            original_request=original_request,
            include_static=include_static,
            role_banner=role_banner,
            require_post_task_learning=require_post_task_learning,
            project_root=project_root,
            project_skill_dir=project_skill_dir,
            compact_team=compact_team,
            operator_context=operator_context,
        )
