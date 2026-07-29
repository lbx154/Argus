"""Typed cross-phase state for ``SkillLoop.run`` — one mission's worth of
context (``MissionContext``) plus the mutable skill-selection/adaptation
bookkeeping (``SkillSelectionState``) that is threaded through the
skill-selection, prompt-building, reviewed-round-hook and post-mission
settlement phases of a single mission.

These are plain data containers with no behavior: ownership of what to do
with the data stays with the mixins in ``loop_skill_selection``,
``loop_prompt``, ``loop_review_hooks`` and ``loop_settlement``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..skills.store import Skill


@dataclass
class MissionContext:
    """Immutable-in-practice facts about the current mission, resolved once
    at the top of ``SkillLoop.run`` and read (never mutated) by every later
    phase.
    """

    workdir: Path
    run_id: str
    task: str
    skill_task: str
    request_anchor: str
    active_vertical: str
    engineer_role_banner: str
    scientist_create_banner: str
    scientist_adaptation_banner: str
    seed_thread_id: str | None
    scope: str


@dataclass
class SkillSelectionState:
    """Mutable Skill-selection/adaptation state for one mission.

    Populated by the initial skill-selection phase and further mutated in
    place by ``continue_adaptor``/``engineer_skill_maintenance`` callbacks
    invoked by ``SupervisedEngineer.run`` across rounds — mirrors the
    original nested-closure ``nonlocal`` mutation exactly, just via
    attribute assignment on a shared object instead of closure cells.
    """

    skill: Skill | None = None
    primary_skills: list[Skill] = field(default_factory=list)
    reference_skills: list[Skill] = field(default_factory=list)
    reviewer_skill_block: str = ""
    strict_skill_hit: bool = False
    nearest_transfer_fallback: bool = False
    low_confidence_transfer_hint: str = ""
    skill_distilled: bool = False
    distill_result: Any = None
    skill_text: str = ""
    adaptive_round_text: str = ""
    skill_name: str | None = None
    learning_target_name: str = ""
    allow_settlement_side_effects: bool = True

    match: Any = None
    matcher_tokens: int = 0
    matcher_input_tokens: int = 0
    matcher_cached_input_tokens: int = 0
    matcher_output_tokens: int = 0
    matcher_premium_requests: float = 0.0

    adaptation_file: Path | None = None
    adaptation_disabled: bool = False
    adaptation_triggers: int = 0
    adaptation_spent: float = 0.0
    rejection_streak: list[dict[str, Any]] = field(default_factory=list)
    method_records: list[dict[str, Any]] = field(default_factory=list)
