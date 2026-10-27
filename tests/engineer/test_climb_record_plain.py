"""The host writes a climb once, plainly, in the task's language; no page reads it back.

Review of PR 334: pages rewrote the host's English climb record with a regex.
The question put to the person stayed English and named roles (F40), and the
mission view showed the raw record as the Reviewer's verdict (F41).
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.core.mission_view import update_mission_view_event
from argus.engineer.round_routing import climb_record

ROLES = ("rung", "nothing changed", "engineer", "reviewer", "planner", "manager", "operator",
         "工程师", "审阅者", "规划者", "经理")


def _plain(text: str) -> bool:
    return not any(word in text.lower() for word in ROLES)


def test_the_question_to_the_person_is_plain_in_the_tasks_language() -> None:
    review = climb_record("operator", chinese=True)

    assert review.operator_question == "这条路没有新进展，需要你决定怎么继续。"
    assert review.review_source == "unchanged_return"
    assert climb_record("manager", chinese=True).operator_question == ""


def test_the_mission_view_shows_the_climb_plainly(tmp_path: Path) -> None:
    reason = climb_record("manager", chinese=True).reason
    view = update_mission_view_event(tmp_path, {
        "type": "round.review.completed", "ts": 1, "round_index": 2, "status": "manager",
        "review_source": "unchanged_return", "reason": reason,
    })

    assert view["review"]["reason"] == "这条路没有新进展，团队正在换个思路。"
    shown = json.dumps([view["review"], view["timeline"], view.get("roles")], ensure_ascii=False)
    assert "这条路没有新进展" in shown and "Nothing changed" not in shown
    assert all(_plain(row.get("detail") or "") for row in view["timeline"])
