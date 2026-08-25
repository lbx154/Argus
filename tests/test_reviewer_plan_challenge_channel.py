"""The Reviewer must be able to say that the plan itself is the obstacle.

Argus already routes such a challenge end to end: the Reviewer raises it, the
Manager decides whose authority it touches, and the Planner is handed the
challenged assumption with its next cycle. The channel was nonetheless
unreachable in practice. Its only key is ``plan_signal="reconsider"``, a word
that appeared in no prompt, and the one example the Reviewer was shown offered
an invalid ``keep``. On top of that the Reviewer's decision event is flat while
the JSON parser read the plan fields only from a nested ``planner_report``, so
a correctly formed challenge was dropped before anyone could route it.

The result was a campaign that could close round after locally correct round
with no way to question the design producing them. These tests pin the channel
open from prompt to Manager decision.
"""

from __future__ import annotations

import json
import re

from argus_skill.manager.plan_challenge import adjudicate_plan_challenge
from argus_skill.reviewer import Reviewer
from argus_skill.reviewer._parsing import _PLAN_SIGNALS, parse_decision_text
from argus_skill.skills.store import SkillStore

_SHOWN_SIGNAL = re.compile(r"(?im)^\s*PLAN_SIGNAL\s*=\s*([a-z_]+)")


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


def test_every_plan_signal_the_reviewer_is_shown_is_one_the_parser_accepts(
    tmp_path,
) -> None:
    shown = set(_SHOWN_SIGNAL.findall(_reviewer_prompt(tmp_path)))
    assert shown, "the Reviewer must see at least one plan_signal example"
    assert shown <= _PLAN_SIGNALS, f"unparseable example values: {shown - _PLAN_SIGNALS}"


def test_the_reviewer_is_told_the_word_that_challenges_the_plan(tmp_path) -> None:
    prompt = _reviewer_prompt(tmp_path)
    assert "reconsider" in prompt
    for field in ("plan_challenge", "plan_alternative", "authority_impact"):
        assert field in prompt


def test_the_reviewer_is_told_a_team_authored_plan_is_revisable(tmp_path) -> None:
    """Authority, not effort, decides what may be replaced."""
    prompt = _reviewer_prompt(tmp_path)
    assert "the team authored for itself is a working choice" in prompt
    assert "manager_contract" in prompt


def test_a_flat_decision_event_carries_the_challenge_to_the_manager() -> None:
    """The documented event shape must survive parsing and reach a decision."""
    decision = parse_decision_text(json.dumps({
        "status": "continue",
        "reason": "Each round repairs a different symptom of one cohort protocol.",
        "next_action": "Certify per cell instead of per cohort.",
        "forward_progress": False,
        "plan_signal": "reconsider",
        "plan_challenge": "The all-or-nothing cohort rule is self-imposed.",
        "plan_alternative": "Certify admissible cells and narrow the claim.",
        "authority_impact": "technical",
    }))

    assert decision is not None
    assert decision.planner_report["plan_signal"] == "reconsider"
    assert decision.planner_report["forward_progress"] is False
    routed = adjudicate_plan_challenge(
        decision.planner_report,
        reviewer_status=decision.status,
    )
    assert routed.action == "replace"
    assert routed.alternative == "Certify admissible cells and narrow the claim."


def test_an_operator_owned_challenge_still_goes_back_to_the_operator() -> None:
    """Reading the flat shape must not let the Reviewer overrule the operator."""
    decision = parse_decision_text(json.dumps({
        "status": "continue",
        "reason": "The exhaustive sweep the operator required is what costs the time.",
        "next_action": "Ask whether a narrower sweep is acceptable.",
        "plan_signal": "reconsider",
        "plan_challenge": "Exhaustive execution dominates the budget.",
        "operator_question": "May we narrow the sweep?",
        "authority_impact": "operator",
    }))

    assert decision is not None
    routed = adjudicate_plan_challenge(
        decision.planner_report,
        reviewer_status=decision.status,
        operator_question=decision.operator_question,
    )
    assert routed.action == "ask_operator"


def test_the_nested_shape_still_parses() -> None:
    """Output already in flight against the older schema must keep working."""
    decision = parse_decision_text(json.dumps({
        "status": "done",
        "reason": "complete",
        "next_action": "",
        "planner_report": {"forward_progress": True, "plan_signal": "continue"},
    }))

    assert decision is not None
    assert decision.planner_report == {
        "forward_progress": True,
        "plan_signal": "continue",
    }


def test_an_unrecognised_signal_is_dropped_rather_than_trusted() -> None:
    decision = parse_decision_text(json.dumps({
        "status": "done",
        "reason": "complete",
        "next_action": "",
        "plan_signal": "keep",
    }))

    assert decision is not None
    assert "plan_signal" not in decision.planner_report
    assert adjudicate_plan_challenge(decision.planner_report).action == "keep"
