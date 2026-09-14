"""Round-loop phase: engineer prompt/context assembly.

Owns deciding whether one round re-sends the full static task/skill contract
or the compact Reviewer-delta continuation (``full_prompt_decision``),
building that prompt, attaching the shared CHECKPOINT.md and the
background-subagent / external-work
advisories, and emitting the ``round.start`` event. This is purely prompt
text assembly — it makes no completion or control-flow decisions.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.event_catalog import EventType
from ..life.context_packet import render_mission_brief
from ..roles.prompts.engineer import MISSION, assemble_round_prompt
from .checkpoint import shared_checkpoint_instructions
from .external_work import render_external_work_advisory

if TYPE_CHECKING:
    from ..core.role_session import RoleSessionCapsule
    from .runner import SupervisedConfig


def mission_stage(context_packet_path: str) -> str:
    """The stage the mission context names, or ``""`` when unreadable.

    The stage selects the role banner and recalled skills inside the static
    prompt, so the FULL/compact decision needs it. Any read or parse problem
    means "unknown", never a broken round.
    """
    if not context_packet_path:
        return ""
    try:
        payload = json.loads(
            Path(context_packet_path).expanduser().read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    if str(payload.get("kind") or "") != "mission_context":
        return ""
    return str(payload.get("stage") or "").strip()


def mission_has_sealed_rounds(context_packet_path: str) -> bool:
    """Whether a prior round of THIS mission sealed a record beside its file.

    Round records land as ``round-*.json`` next to the mission context, one
    directory per mission. Their presence is the evidence that a resumed
    provider session has already worked this very mission — a brand-new
    mission whose objective merely repeats an old one has an empty directory.
    """
    if not context_packet_path:
        return False
    try:
        root = Path(context_packet_path).expanduser().parent
        return any(root.glob("round-*.json"))
    except OSError:
        return False


def full_prompt_decision(
    *,
    round_index: int,
    session_policy: str,
    session_action: str,
    rotation_reason: str,
    compact_continuation_prompts: bool,
    full_round_policy: str,
    mission_has_history: bool,
    stage: str,
    stage_at_last_full: str,
) -> tuple[bool, str]:
    """Decide FULL vs compact for one Engineer round, with the reason why.

    A provider session receives the full static text on the round that
    creates it — a session only ever starts on a FULL round — so a resumed
    session needs the full text again only when what it holds may be stale.
    Under the default ``session`` policy that means: compact prompts are
    disabled, the policy is fresh-per-round, the session was just created or
    rotated (rotation already covers an objective or workdir change and an
    explicit Reviewer redirection signal), the stage changed since the last
    full prompt, or a first round resumes a session with no sealed prior
    round for this mission. ``legacy`` keeps the historical round-1-always-
    full behavior for rollback.
    """
    if not compact_continuation_prompts:
        return True, "compact_prompts_disabled"
    if session_policy == "fresh":
        # Every round starts a provider session with no memory of the last.
        return True, "fresh_policy"
    if session_action == "rotated":
        return True, f"session_rotated:{rotation_reason or 'unspecified'}"
    if session_action != "resumed":
        return True, "session_fresh"
    if full_round_policy == "legacy":
        if round_index == 1:
            return True, "first_round_legacy"
        return False, "resumed_session"
    if stage and stage_at_last_full and stage != stage_at_last_full:
        return True, "stage_changed"
    if round_index == 1:
        if not mission_has_history:
            return True, "first_round_without_prior_rounds"
        return False, "resumed_session_with_prior_rounds"
    return False, "resumed_session"


class RoundPromptMixin:
    """Mixin providing ``SupervisedEngineer``'s prompt-assembly phase."""

    def _vertical_role_context(
        self,
        *,
        supervised_config: "SupervisedConfig",
        workdir: Path,
    ) -> str:
        """The active vertical's per-turn facts (research notes, GPU memory in use).

        Resolved with the same vertical, state root, stage and operation the
        static banner was resolved with, so the facts match the policy; placed
        in the round's tail so the static text ahead of it stays identical
        between rounds. Any failure means no block, never a broken round.
        """
        engineer_config = getattr(self, "engineer_config", None)
        reviewer_config = getattr(self, "reviewer_config", None)
        if engineer_config is None and reviewer_config is None:
            return ""
        try:
            state_root = Path(
                getattr(engineer_config, "vertical_state_root", None)
                or getattr(reviewer_config, "vertical_state_root", None)
                or workdir
            )
            vertical = (
                str(getattr(reviewer_config, "active_vertical", "") or "")
                .strip()
                .lower()
            )
            if not vertical:
                from ..skills.vertical_select import resolve_vertical_if_decided

                vertical = str(resolve_vertical_if_decided(state_root) or "")
            if not vertical:
                return ""
            operation = str(
                getattr(supervised_config, "engineer_operation", "") or MISSION
            )
            stage: str | None = None
            if operation != MISSION:
                from ..skills.stage_machine import current_stage

                stage = current_stage(state_root)
            from ..roles.prompts import resolve_role_prompt
            from ..roles.prompts.engineer import mission_request

            return resolve_role_prompt(
                mission_request(
                    state_root,
                    vertical=vertical,
                    altitude_root=workdir,
                    stage=stage,
                    operation=operation,
                    include_role_context=True,
                )
            ).role_context
        except Exception:  # noqa: BLE001 - live context is advisory
            return ""

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
        #
        # ``getattr`` for the newer config/capsule fields keeps the partial
        # ``SimpleNamespace`` drivers in tests/test_checkpoint.py working.
        context_packet_path = str(
            getattr(supervised_config, "context_packet_path", "") or ""
        )
        stage = mission_stage(context_packet_path)
        include_static, prompt_mode_reason = full_prompt_decision(
            round_index=round_index,
            session_policy=role_session.policy,
            session_action=role_session.action,
            rotation_reason=str(
                getattr(role_session, "rotation_reason", "") or ""
            ),
            compact_continuation_prompts=(
                supervised_config.compact_continuation_prompts
            ),
            full_round_policy=str(
                getattr(supervised_config, "engineer_full_round_policy", "")
                or "session"
            ),
            # Only round 1 consults mission history; skip the directory scan on
            # every later round.
            mission_has_history=(
                round_index == 1
                and mission_has_sealed_rounds(context_packet_path)
            ),
            stage=stage,
            stage_at_last_full=str(
                getattr(self, "_stage_at_last_full_prompt", "") or ""
            ),
        )
        if include_static and stage:
            self._stage_at_last_full_prompt = stage
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
        mission_brief = render_mission_brief(context_packet_path)
        capsule_block = role_session.prompt_block()
        # A compact continuation must still point back at the files that carry
        # the task's terms and current state, so the model can read them
        # itself. The capsule block already names them when a capsule exists;
        # this covers the compact rounds that run without one.
        continuation_references = ""
        if not include_static and not capsule_block and context_packet_path:
            packet = Path(context_packet_path).expanduser()
            continuation_references = (
                "## Where the mission state lives\n"
                f"The task as agreed: `{packet}`\n"
                f"The latest reviewed round: `{packet.parent / 'latest.json'}`\n"
                "Read them yourself when this continuation prompt is not "
                "enough context."
            )
        checkpoint_block = "\n\n".join(
            block
            for block in (
                mission_brief,
                capsule_block,
                continuation_references,
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
            role_context=self._vertical_role_context(
                supervised_config=supervised_config,
                workdir=workdir,
            ),
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
                    "prompt_mode_reason": prompt_mode_reason,
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
