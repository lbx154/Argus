"""Agent-native Skill-library preparation.

Agents receive library paths and choose what to read. Optional domain setup is
owned by the active vertical through the core contract, never by name branches
in this generic layer.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from ..core.vertical_contract import VerticalLibraryContext
from .loop_state import MissionContext, SkillLibraryState

log = logging.getLogger(__name__)


class SkillLibraryMixin:
    def _prepare_skill_libraries(self, mission: MissionContext) -> SkillLibraryState:
        required_skill_paths = self._prepare_vertical_libraries(mission)
        state = SkillLibraryState()
        state.skill_libraries = self.engineer_mission.libraries(
            required_relative_paths=required_skill_paths,
        )
        # Paths and discovery instructions only: no runtime matching, adaptation,
        # copying, or Skill-body injection.
        state.skill_text = state.skill_libraries.block
        state.reviewer_skill_block = self.reviewer.mission.libraries().block
        return state

    def _prepare_vertical_libraries(self, mission: MissionContext) -> tuple[str, ...]:
        """Let the provider run optional domain setup with explicit inputs."""
        required_skill_paths: list[str] = []
        try:
            from ..core.pipeline_state import pipeline_state_exists
            from ..verticals._base import load_vertical_contract
            from .stage_machine import current_stage

            state_root = Path(self.config.vertical_state_root or mission.workdir)
            if not pipeline_state_exists(state_root):
                log.warning(
                    "vertical Skill-library state cannot be determined: "
                    "PIPELINE_STATE.json is missing at resolved state root %s",
                    state_root,
                )
            stage = current_stage(state_root) or ""
            contract = load_vertical_contract(
                mission.active_vertical,
                project_root=mission.workdir,
            )
            contract.prepare_libraries(VerticalLibraryContext(
                workdir=mission.workdir,
                state_root=state_root,
                stage=str(stage).strip().lower(),
                objective=mission.skill_task,
                direction=(
                    self.config.continuous_objective.strip()
                    or mission.request_anchor
                ),
                workflow_mode=self.config.workflow_mode,
                paper_mission=self.config.paper_mission,
                team_task_id=(
                    os.environ.get("ARGUS_SKILL_TEAM_TASK_ID", "").strip() or None
                ),
                runner=self.engineer_runner,
                model=self.config.engineer_model,
                emit=self._emit,
                required_skill_paths=required_skill_paths,
            ))
        except Exception:  # noqa: BLE001 — optional domain preparation is non-blocking
            log.debug("vertical Skill-library preparation skipped", exc_info=True)
        return tuple(dict.fromkeys(required_skill_paths))

    def _adapt_after_rejections(
        self,
        mission: MissionContext,
        state: SkillLibraryState,
        rounds: list[object],
    ) -> str:
        _ = (mission, state, rounds)
        # The Engineer may independently revisit the library after Reviewer
        # feedback. The runtime does not select or inject an alternative.
        return ""
