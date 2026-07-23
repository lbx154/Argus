"""Round-loop phase: engineer prompt/context assembly.

Owns building the per-round engineer prompt (static task/skill contract on
round 1 or when compact continuation prompts are disabled; otherwise a
compact Reviewer-delta prompt), attaching the shared CHECKPOINT.md and the
background-subagent / external-work
advisories, and emitting the ``round.start`` event. This is purely prompt
text assembly — it makes no completion or control-flow decisions.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.event_catalog import EventType
from ..roles.prompts.engineer import assemble_round_prompt
from .background_subagents import render_background_subagents_advisory
from .checkpoint import shared_checkpoint_instructions
from .external_work import render_external_work_advisory

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
        workdir: Path,
        on_event: Callable[[dict], None] | None,
    ) -> str:
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
        checkpoint_block = shared_checkpoint_instructions(
            checkpoint_path,
            role="engineer",
        )
        background_advisory = (
            render_background_subagents_advisory(workdir)
            if supervised_config.background_subagent_advisory
            else ""
        )
        external_work_advisory = render_external_work_advisory(workdir)
        engineer_prompt = assemble_round_prompt(
            engineer_prompt,
            checkpoint_block=checkpoint_block,
            background_advisory=background_advisory,
            external_work_advisory=external_work_advisory,
        )
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
        return engineer_prompt
