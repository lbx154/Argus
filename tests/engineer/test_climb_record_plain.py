"""The host writes a climb once, plainly, in the task's language; no page reads it back.

Review of PR 334: pages rewrote the host's English climb record with a regex.
The decision card dropped the Reviewer's words (F39), the question put to the
person stayed English and named roles (F40), and the mission view showed the
raw record as the Reviewer's verdict (F41).
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.core.models import ReviewDecision
from argus.core.operator_decision import build_operator_decision
from argus.engineer.round_routing import climb_record
from tests.mission_view_events import project_event

ROLES = ("rung", "nothing changed", "engineer", "reviewer", "planner", "manager", "operator",
         "工程师", "审阅者", "规划者", "经理")


def _plain(text: str) -> bool:
    return not any(word in text.lower() for word in ROLES)


def test_the_card_keeps_the_reviewers_reason_when_work_climbs_to_the_person() -> None:
    handed = ReviewDecision(
        status="manager", next_action="",
        reason="The benchmark numbers contradict the claim in section 3.",
    )
    review = climb_record("operator", chinese=False, handed=handed)
    card = build_operator_decision(
        item_id="t", title="Benchmark the kernel", reason=review.reason, question=review.operator_question,
    )

    assert "The benchmark numbers contradict the claim in section 3." in card["reason"]
    assert card["reason"].startswith("This path is making no new progress")
    assert review.reviewer_words["reason"] == handed.reason


def test_the_question_to_the_person_is_plain_in_the_tasks_language() -> None:
    review = climb_record("operator", chinese=True)

    assert review.operator_question == "这条路没有新进展，需要你决定怎么继续。"
    assert review.review_source == "unchanged_return"
    assert climb_record("manager", chinese=True).operator_question == ""


def test_the_mission_view_shows_the_climb_plainly(tmp_path: Path) -> None:
    reason = climb_record("manager", chinese=True).reason
    view = project_event(tmp_path, {
        "type": "round.review.completed", "ts": 1, "round_index": 2, "status": "manager",
        "review_source": "unchanged_return", "reason": reason,
    })

    assert view["review"]["reason"] == "这条路没有新进展，团队正在换个思路。"
    shown = json.dumps([view["review"], view["timeline"], view.get("roles")], ensure_ascii=False)
    assert "这条路没有新进展" in shown and "Nothing changed" not in shown
    assert all(_plain(row.get("detail") or "") for row in view["timeline"])
