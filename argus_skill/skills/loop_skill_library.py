"""Agent-native Skill-library preparation.

The previous matcher, nearest-transfer selector, adapter, and automatic Skill
mutation pipeline have been removed.  Every Agent receives library paths and
searches/reads Skill Markdown with its own tools.
"""
from __future__ import annotations

import logging
import os

from .loop_state import MissionContext, SkillLibraryState

log = logging.getLogger(__name__)


class SkillLibraryMixin:
    def _prepare_skill_libraries(self, mission: MissionContext) -> SkillLibraryState:
        self._run_venue_research(mission)
        state = SkillLibraryState()
        state.skill_libraries = self.engineer_mission.libraries()
        # ``block`` contains paths and discovery instructions only.  No Skill
        # document is parsed, selected, adapted, or copied into the prompt.
        state.skill_text = state.skill_libraries.block
        state.reviewer_skill_block = state.skill_libraries.block
        self._maybe_seed_idea_candidates(mission)
        return state

    def _adapt_after_rejections(
        self,
        mission: MissionContext,
        state: SkillLibraryState,
        rounds: list[object],
    ) -> str:
        _ = (mission, state, rounds)
        # The Engineer may independently revisit the library after Reviewer
        # feedback.  The runtime does not create or inject an alternative.
        return ""

    def _run_venue_research(self, mission: MissionContext) -> None:
        if self.config.workflow_mode == "direct":
            return
        if os.environ.get("ARGUS_SKILL_VENUE_RESEARCH", "1").strip().lower() in {
            "0",
            "false",
            "no",
            "off",
        }:
            return
        try:
            from .stage_machine import current_stage
            from .venue_research import needs_venue_research, research_venue_profile
            from .vertical_select import _persisted_vertical

            if (
                self.config.paper_mission
                and (_persisted_vertical(mission.workdir) or "research") == "research"
                and (current_stage(mission.workdir) or "").strip().lower()
                in {"research", "plan", "benchmark", "run", "analysis"}
                and needs_venue_research(mission.workdir)
            ):
                self._emit(
                    {
                        "type": "venue.research.started",
                        "text": "live web search: selecting/researching target venue",
                    }
                )
                ok = research_venue_profile(
                    self.engineer_runner,
                    mission.workdir,
                    model=self.config.engineer_model,
                )
                self._emit(
                    {
                        "type": "venue.research.completed",
                        "ok": ok,
                        "text": (
                            "built research/VENUE_PROFILE.json"
                            if ok
                            else "venue research produced no profile"
                        ),
                    }
                )
        except Exception:  # noqa: BLE001 - optional source remains fail-open
            log.debug("venue-research hook skipped", exc_info=True)

    def _maybe_seed_idea_candidates(self, mission: MissionContext) -> None:
        if os.environ.get("ARGUS_SKILL_IDEA_SEARCH", "1").strip().lower() in {
            "0",
            "false",
            "no",
            "off",
        }:
            return
        try:
            from .idea_search import _already_seeded, augment_idea_candidates
            from .stage_machine import current_stage
            from .vertical_select import _persisted_vertical

            is_research = (
                _persisted_vertical(mission.workdir) or "research"
            ) == "research"
            if (
                self.config.paper_mission
                and is_research
                and (current_stage(mission.workdir) or "").strip().lower()
                == "research"
                and not _already_seeded(mission.workdir)
            ):
                self._emit(
                    {
                        "type": "idea.search.started",
                        "text": "live web search: seeding candidate ideas",
                    }
                )
                count = augment_idea_candidates(
                    self.engineer_runner,
                    mission.workdir,
                    direction=(
                        self.config.continuous_objective.strip()
                        or mission.request_anchor
                    ),
                    model=self.config.engineer_model,
                )
                self._emit(
                    {
                        "type": "idea.search.completed",
                        "count": count,
                        "text": f"appended {count} candidate idea(s)",
                    }
                )
        except Exception:  # noqa: BLE001
            log.debug("idea-search hook skipped", exc_info=True)
