"""The Reviewer must be able to say that the plan itself is the obstacle.

Argus already routes such a challenge end to end: the Reviewer raises it, the
Manager decides whose authority it touches, and the Planner is handed the
challenged assumption with its next cycle. The channel was nonetheless
unreachable in practice: its signal was a word that appeared in no prompt, and
the one example the Reviewer was shown offered an invalid ``keep``.

The result was a campaign that could close round after locally correct round
with no way to question the design producing them. These tests pin the channel
open from prompt to Manager decision.
"""

from __future__ import annotations

from argus.manager.plan_challenge import adjudicate_plan_challenge
from argus.reviewer import Reviewer
from argus.reviewer.tools import review_actions
from argus.skills.store import SkillStore
from tests.hand_off import hand_off


def _reviewer_prompt(tmp_path) -> str:
    return Reviewer(
        runner=None,
        skill_store=SkillStore(tmp_path / "skills"),
        memory_maintenance_enabled=False,
    )._build_prompt(
        objective="Certify the experiment cohort.",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="The redaction bug is repaired and the run completed 15 of 16 cells.",
        main_error=None,
        working_dir=tmp_path,
    )


def test_a_plan_challenge_is_the_reviewers_line_to_the_planner(tmp_path) -> None:
    assert "`to=planner`" in _reviewer_prompt(tmp_path)
    actions = review_actions()
    assert "authority_impact" not in actions.tools[0]["inputSchema"]["properties"]
    actions.dispatch(*hand_off("planner", "Each round repairs a symptom of one cohort rule; certify per cell."))
    assert actions.handoff.value.status == "replan_requested"
    routed = adjudicate_plan_challenge(
        reviewer_status=actions.handoff.value.status,
        review_reason=actions.handoff.value.reason,
        next_action=actions.handoff.value.next_action,
    )
    assert routed.action == "replace"
