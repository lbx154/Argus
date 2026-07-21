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
from ..core.task_contract import EFFECTIVE_TASK_CONTRACT
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
            allow_self_review=self.config.engineer_self_review_enabled,
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
        return (
            prompt
            + "\n\n## LIVE MANAGER / OPERATOR DIRECTIVES — HIGHEST PRIORITY\n"
            + "These directives may stop, narrow, or correct the current mission. "
            + "They do not silently broaden a structured bounded task or cross its "
            + "pipeline stage. If a directive materially replaces the current "
            + "bounded objective, preserve state, update CHECKPOINT.md, and request "
            + "Reviewer/Planner replanning instead of executing the new scope here.\n"
            + "\n".join(f"- {item}" for item in guidance)
        )

    @staticmethod
    def _build_engineer_prompt(
        *,
        task: str,
        skill_text: str,
        next_action: str | None,
        original_request: str = "",
        include_static: bool = True,
        role_banner: str = "",
        allow_self_review: bool = False,
        matched_skill_name: str = "",
        require_post_task_learning: bool = False,
        force_post_task_learning: bool = False,
        file_read_budget: int = 12,
        test_run_budget: int = 3,
    ) -> str:
        # STATIC remains byte-stable for provider prefix caching. Autonomous
        # Engineer calls are always fresh and receive the full prompt.
        sections: list[str] = []
        delta_sections: list[str] = []
        sections.append(EFFECTIVE_TASK_CONTRACT)
        if role_banner.strip():
            sections.append("## Active vertical role\n" + role_banner.strip())
        if skill_text:
            sections.append("## Skill playbook (read first)\n" + skill_text)
        if original_request.strip():
            sections.append(
                "## Original operator request\n"
                "Higher-priority live operator instructions may update this; "
                "lower-authority guidance may not silently change it.\n\n"
                + original_request.strip()
            )
        sections.append("## Current mission task\n" + task)
        if next_action:
            delta_sections.append(
                "## Reviewer guidance from prior round\n"
                "The previous round was judged incomplete. Address the\n"
                "following before declaring done:\n\n"
                + next_action
            )
        sections.append(
            "## This turn\n"
            "Land one coherent, verifiable increment; update "
            "CHECKPOINT.md, then yield. Pure reading without an artifact or "
            "measurement is not progress.\n"
            "Work in the current directory. Unless required, do not write "
            "planning/spec/brief documents, initialize Git, branch/worktree, commit, "
            "spawn subagents, or invoke meta-workflows.\n"
            f"Budget: inspect about {max(1, int(file_read_budget))} relevant files "
            "before editing and avoid rereads; run at most "
            f"{max(1, int(test_run_budget))} focused verification commands plus the "
            "decisive verifier. Exceed only after a concrete failure or code change. "
            "Ignore `.autors` unless retaining durable learning."
        )
        sections.append(
            "## Handoff\n"
            "End with a short, natural account of what changed and the decisive "
            "check or observation. Do not recite a checklist or build an evidence "
            "packet; include only details the next researcher needs.\n\n"
            + (
                "Use `review=skip` only for low-risk bounded work with a passing "
                "verifier. Require review for failures, risky cross-module changes, "
                "or unsettled judgment. Do not spawn a Reviewer subagent; for "
                "`review=required`, yield for a fresh Reviewer session. Request skill "
                "maintenance only for durable learning.\n"
                if allow_self_review
                else
                "`review=required`; don't spawn Reviewer subagents. Yield for fresh "
                "Reviewer session.\n"
            )
        )
        if require_post_task_learning and force_post_task_learning:
            required_action = "update" if matched_skill_name else "create"
            target = (
                f" the matched skill `{matched_skill_name}`"
                if matched_skill_name
                else " one reusable Engineer skill"
            )
            sections.append(
                "## Required self-evolution\n"
                "After verification, select `" + required_action + "` in the internal "
                "control file for"
                + target
                + "; the harness resumes this session to author it. Also retain one "
                "concise `.autors/<project>/wiki/` note with the reusable mechanism, "
                "failed approach, and decisive verification."
            )
        elif require_post_task_learning:
            sections.append(
                "## Durable learning\n"
                "Use `skill_action=create|update` only for a verified durable mechanism "
                "that changes future work; otherwise use `skill_action=none`. Write a "
                "wiki note only for similarly durable project knowledge."
            )
        static_text = "\n\n".join(sections)
        delta_text = "\n\n".join(delta_sections)
        if include_static:
            return static_text + ("\n\n" + delta_text if delta_text else "")
        compact = (
            "## Continuation turn\n"
            "Read the shared CHECKPOINT.md first. Execute its current Next Action "
            "and the Reviewer guidance below. Do not repeat an unchanged failing "
            "command; reduce it to the cheapest decisive diagnostic. The original "
            "task, active vertical, and repository instructions remain binding.\n\n"
            "## Handoff\n"
            "End with a concise natural summary and decisive check."
        )
        return compact + ("\n\n" + delta_text if delta_text else "")
