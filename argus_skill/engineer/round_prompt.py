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
from ..life.context_packet import render_mission_brief
from ..roles.prompts.engineer import assemble_round_prompt
from .checkpoint import shared_checkpoint_instructions
from .external_work import render_external_work_advisory

if TYPE_CHECKING:
    from ..core.role_session import RoleSessionCapsule
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
        role_session: "RoleSessionCapsule",
        on_event: Callable[[dict], None] | None,
    ) -> str:
        # Cross-round role context comes from CHECKPOINT.md, not duplicated
        # free-form reviewer prose in the next Engineer prompt.
        include_static = (
            round_index == 1
            or not supervised_config.compact_continuation_prompts
            or (
                role_session.policy != "fresh"
                and role_session.action in {"fresh", "rotated"}
            )
        )
        engineer_prompt = engineer_prompt_builder(
            reviewer_next_action,
            include_static,
        )
        rotation_block = ""
        if (
            round_index > 1
            and role_session.policy != "fresh"
            and role_session.action in {"fresh", "rotated"}
        ):
            rotation_block = (
                "## Session rotation — continue the current mission stage\n"
                f"This is mission round {round_index}, not round 1. Provider context "
                "was rotated; do not restart a staged protocol or repeat completed "
                "work. The Reviewer guidance in this prompt is the approval for the "
                "current step: execute it now and do not yield to ask for that "
                "approval again. Read the canonical checkpoint and latest reviewed "
                "handoff below first."
            )
        mission_brief = render_mission_brief(
            getattr(supervised_config, "context_packet_path", "")
        )
        checkpoint_block = "\n\n".join(
            block
            for block in (
                mission_brief,
                role_session.prompt_block(),
                rotation_block,
                # Unconditional, including round 1. The baton has to be WRITTEN
                # by the round before the one that reads it, and gating this on
                # `round_index > 1` meant round 1 was never told the file
                # existed: it finished, sealed a handoff record advertising
                # `checkpoint.path`, and round 2 opened that path to a missing
                # file and restarted the work from nothing. Round 1's findings
                # were only ever in a rotated-away provider context.
                #
                # This does not impose ceremony on a one-round mission — the
                # instruction itself is already conditional ("create or update
                # it only when another round needs current state, evidence
                # paths, blockers, or a next action"), so a mission that ends in
                # round 1 still writes nothing.
                shared_checkpoint_instructions(
                    checkpoint_path,
                    role="engineer",
                ),
            )
            if block
        )
        external_work_advisory = render_external_work_advisory(
            workdir,
            include_subagents=supervised_config.background_subagent_advisory,
        )
        engineer_prompt = assemble_round_prompt(
            engineer_prompt,
            checkpoint_block=checkpoint_block,
            background_advisory="",
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
                    "role_session_policy": role_session.policy,
                    "role_session_action": role_session.action,
                    "text": (
                        f"engineer round {round_index} "
                        f"({role_session.action} session)"
                    ),
            })
        return engineer_prompt
