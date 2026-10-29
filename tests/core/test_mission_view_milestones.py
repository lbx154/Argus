"""The team's milestones in the Mission View, for the conversation and the Manager.

Hosted trial s-dacac579 (2026-10-29): for about an hour the conversation showed
only "正在执行：… · 工程师：…" while the log recorded two restarts, a review
that sent round 1 back with its reason, two more rounds and new files.
"""
from __future__ import annotations

from pathlib import Path

from argus.core.mission_view._milestones import MILESTONE_LIMIT
from tests.mission_view_events import project_event

TASK = "2873b58e7a44"
SENT_BACK = "已选定计算机视觉研究方向并建立真实数据运行环境，但切换后的关键比较路径尚未在实际短跑中验证，需补齐后才能确认本轮完成。"


def emit(root: Path, event_type: str, ts: float, **payload) -> dict:
    return project_event(root, {"type": event_type, "ts": ts, **payload})


def _shape(view: dict) -> list[tuple]:
    return [(row["kind"], row.get("round"), row.get("status") or row.get("title") or row.get("error") or
             row.get("paths")) for row in view["milestones"]]


def test_rounds_reviews_restarts_and_written_files_are_milestones(tmp_path: Path) -> None:
    emit(tmp_path, "life.mission.started", 1, item_id=TASK, title="确定视觉研究方向")
    emit(tmp_path, "round.start", 2, item_id=TASK, round_index=1)
    emit(tmp_path, "life.mission.requeued", 3, item_id=TASK, title="确定视觉研究方向",
         error="orphaned: previous process did not finish")
    emit(tmp_path, "round.start", 4, item_id=TASK, round_index=1)
    # The Engineer's writes, as two backends report them; another role's and a read are not.
    emit(tmp_path, "engineer.progress", 5, item_id=TASK, agent_layer="engineer", kind="tool_use",
         tool_name="apply_patch", text="apply_patch: *** Begin Patch\n*** Add File: METHOD.md\n+x\n*** Update File: RESEARCH_NOTES.md")
    emit(tmp_path, "engineer.progress", 6, item_id=TASK, agent_layer="engineer", kind="file_change",
         changes=["figures/ablation.png", "METHOD.md"])
    emit(tmp_path, "engineer.progress", 6.5, item_id=TASK, agent_layer="reviewer", kind="file_change", changes=["REVIEW.md"])
    emit(tmp_path, "engineer.progress", 6.6, item_id=TASK, agent_layer="engineer", kind="tool_use",
         tool_name="view", text='view: {"path": "README.md"}')
    emit(tmp_path, "round.main.completed", 7, item_id=TASK, round_index=1, exit_code=0)
    emit(tmp_path, "round.review.completed", 8, item_id=TASK, round_index=1, status="engineer",
         review_source="reviewer", reason=SENT_BACK)
    view = emit(tmp_path, "round.start", 9, item_id=TASK, round_index=2)

    assert _shape(view) == [
        ("task_started", None, "确定视觉研究方向"),
        ("round_started", 1, None),
        ("task_requeued", None, "确定视觉研究方向"),
        ("round_started", 1, None),
        ("files", 1, ["RESEARCH_NOTES.md", "figures/ablation.png", "METHOD.md"]),
        ("round_finished", 1, None),
        ("review", 1, "engineer"),
        ("round_started", 2, None),
    ]
    review = view["milestones"][6]
    # The Reviewer's own words, kept as recorded.
    assert (review["reason"], review["source"], review["item_id"]) == (SENT_BACK, "reviewer", TASK)


def test_task_results_and_stage_moves_stay_with_the_chat_messages_that_tell_them(tmp_path: Path) -> None:
    emit(tmp_path, "life.mission.started", 1, item_id="a", title="A")
    emit(tmp_path, "life.mission.completed", 2, item_id="a", status="done", success=True)
    view = emit(tmp_path, "life.manager.stage_decision", 3, action="advance", target_stage="experiment")

    assert [row["kind"] for row in view["milestones"]] == ["task_started"]


def test_an_earlier_tasks_review_and_a_hold_are_kept(tmp_path: Path) -> None:
    emit(tmp_path, "life.mission.started", 1, item_id="old", title="Old")
    emit(tmp_path, "life.mission.started", 2, item_id="new", title="New")
    # The view's current review belongs to the new task; the old task's verdict is still its milestone.
    emit(tmp_path, "round.review.completed", 3, item_id="old", round_index=1, status="done", reason="Accepted.")
    view = emit(tmp_path, "life.budget.pause", 4, text="The daily budget is spent.")

    assert [(row["kind"], row["item_id"]) for row in view["milestones"][-2:]] == [("review", "old"), ("stopped", "")]
    assert view["milestones"][-1]["text"] == "The daily budget is spent."


def test_the_milestones_are_bounded(tmp_path: Path) -> None:
    for index in range(MILESTONE_LIMIT + 5):
        view = emit(tmp_path, "round.start", index, item_id="t", round_index=index + 1)

    assert len(view["milestones"]) == MILESTONE_LIMIT
    assert view["milestones"][-1]["round"] == MILESTONE_LIMIT + 5
