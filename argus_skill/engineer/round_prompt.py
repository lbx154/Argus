"""Round-loop phase: engineer prompt/context assembly.

Owns building the per-round engineer prompt (static task/skill contract on
round 1 or when compact continuation prompts are disabled; otherwise a
compact Reviewer-delta prompt), attaching the shared CHECKPOINT.md /
engineer-control instructions and the background-subagent / external-work
advisories, and emitting the ``round.start`` event. This is purely prompt
text assembly — it makes no completion or control-flow decisions.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.event_catalog import EventType
from .background_subagents import render_background_subagents_advisory
from .checkpoint import shared_checkpoint_instructions
from .external_work import render_external_work_advisory
from .self_review import (
    engineer_control_instructions,
    engineer_control_path,
    prepare_engineer_control,
)

if TYPE_CHECKING:
    from .runner import SupervisedConfig


class RoundPromptMixin:
    """Mixin providing ``SupervisedEngineer``'s prompt-assembly phase."""

    def _assemble_round_prompt(
        self,
        *,
        round_index: int,
        supervised_config: "SupervisedConfig",
        engineer_prompt_builder: Callable[[str | None, bool], str],
        reviewer_next_action: str | None,
        checkpoint_path: Path | None,
        control_scope: str,
        workdir: Path,
        on_event: Callable[[dict], None] | None,
    ) -> tuple[str, Path]:
        # Cross-round role context comes from CHECKPOINT.md, not duplicated
        # free-form reviewer prose in the next Engineer prompt.
        include_static = (
            round_index == 1
            or not supervised_config.compact_continuation_prompts
        )
        engineer_prompt = engineer_prompt_builder(
            reviewer_next_action,
            include_static,
        )
        control_path = engineer_control_path(
            workdir=workdir,
            checkpoint_path=checkpoint_path,
            round_index=round_index,
            control_scope=control_scope,
        )
        prepare_engineer_control(control_path)
        delta_tail: list[str] = []
        checkpoint_block = shared_checkpoint_instructions(
            checkpoint_path,
            role="engineer",
        )
        if checkpoint_block:
            delta_tail.append(checkpoint_block)
        delta_tail.append(
            engineer_control_instructions(
                control_path,
                allow_self_review=supervised_config.allow_engineer_self_review,
                allow_skill_maintenance=(
                    supervised_config.allow_engineer_skill_maintenance
                ),
            )
        )
        background_advisory = (
            render_background_subagents_advisory(workdir)
            if supervised_config.background_subagent_advisory
            else ""
        )
        if background_advisory:
            delta_tail.append(background_advisory)
        external_work_advisory = render_external_work_advisory(workdir)
        if external_work_advisory:
            delta_tail.append(external_work_advisory)
        if delta_tail:
            engineer_prompt = engineer_prompt + "\n\n" + "\n\n".join(delta_tail)
        if on_event:
            on_event({
                "type": EventType.ROUND_START,
                "round_index": round_index,
                    # Kept for readers of the historical event schema.
                    "round": round_index,
                    "round_max": supervised_config.max_rounds,
                    "prompt_mode": "full" if include_static else "compact",
                    "prompt_chars": len(engineer_prompt),
                    "prompt_estimated_tokens": (len(engineer_prompt) + 3) // 4,
                    "text": f"engineer round {round_index} (fresh session)",
            })
        return engineer_prompt, control_path
