"""Team completion must reach the operator without a follow-up question."""

from __future__ import annotations

import json

from argus_skill.core.transcript import append_turn
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig


class _Runner:
    pass


def test_continuous_mission_completion_publishes_once(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="keep improving",
            open_ended=True,
        ),
    )
    event = {
        "type": "life.mission.completed",
        "item_id": "task-1",
        "title": "Fix the first issue",
        "success": True,
        "status": "done",
        "summary": "Updated the parser and passed focused regression tests.",
        "outcome": {"review_status": "done"},
    }

    assert supervisor._emit(event) is True
    assert supervisor._emit(event) is True

    transcript = [
        json.loads(line)
        for line in (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(transcript) == 1
    assert transcript[0]["role"] == "argus"
    assert "Task continued · Fix the first issue · review=done" in transcript[0]["text"]
    assert (
        "Progress: Updated the parser and passed focused regression tests."
        in transcript[0]["text"]
    )
    assert "Planner is selecting the next work item" in transcript[0]["text"]
    ui_events = [
        event
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if (event := json.loads(line)).get("type") == "ui.argus"
    ]
    assert len(ui_events) == 1
    assert ui_events[0]["summary"] == (
        "Updated the parser and passed focused regression tests."
    )
    assert ui_events[0]["campaign_continues"] is True
    assert ui_events[0]["delivery"] is None


def test_bounded_completion_says_the_task_is_finished(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=False, open_ended=False),
    )

    supervisor._emit({
        "type": "life.mission.completed",
        "item_id": "task-2",
        "title": "One bounded fix",
        "success": True,
        "status": "done",
        "outcome": {
            "review_status": "not_assessed",
            "final_submission_certified": True,
        },
    })

    text = json.loads(
        (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )["text"]
    assert text == (
        "Task completed · One bounded fix\n"
        "The requested work is complete."
    )


def test_completion_summary_uses_the_operator_language(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=False, open_ended=False),
    )
    append_turn(memory.root, "operator", "请修复解析器。")

    supervisor._emit({
        "type": "life.mission.completed",
        "item_id": "task-zh",
        "title": "Repair parser",
        "success": True,
        "status": "done",
        "summary": "修复了解析器并通过回归测试。",
        "outcome": {"review_status": "done"},
    })

    text = json.loads(
        (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )["text"]
    assert "任务已完成" in text
    assert "本次完成: 修复了解析器并通过回归测试。" in text
    assert "Mission summary" not in text


def test_bounded_increment_does_not_claim_project_or_stage_completion(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=False, open_ended=False),
    )

    supervisor._emit({
        "type": "life.mission.completed",
        "item_id": "task-3",
        "title": "Write the paper draft",
        "success": True,
        "status": "done",
        "outcome": {
            "review_status": "done",
            "stage_certification": "intentionally_skipped",
        },
    })

    text = json.loads(
        (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )["text"]
    assert text == (
        "Task completed · Write the paper draft · review=done\n"
        "The requested work is complete."
    )


def test_failed_mission_explains_reason_and_next_action(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=False, open_ended=False),
    )

    supervisor._emit({
        "type": "life.mission.completed",
        "item_id": "task-failed",
        "title": "Run the external validator",
        "success": False,
        "status": "blocked",
        "stop_reason": "The required credentials are unavailable.",
        "next_action": "Provide credentials or remove the external requirement.",
    })

    text = json.loads(
        (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )["text"]
    assert text.startswith("Cannot continue yet: Run the external validator.")
    assert "Reason: Continuing requires an access credential from you." in text
    assert "Your decision: Provide credentials" in text
    assert "Team ended" not in text


def test_technical_blocker_does_not_pretend_a_human_decision_is_needed(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=False, open_ended=False),
    )

    supervisor._emit({
        "type": "life.mission.completed",
        "item_id": "task-timeout",
        "title": "Measure the large kernel row",
        "success": False,
        "status": "blocked",
        "stop_reason": "row exceeded timeout_s=300",
        "next_action": "Run the isolated one-row diagnostic.",
        "summary": "The large row timed out; smaller rows remain valid.",
        "operator_question": "",
    })

    text = json.loads(
        (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )["text"]
    assert "does not prove the idea is wrong" in text
    assert "Next: Run the isolated one-row diagnostic." in text
    assert "Mission summary: The large row timed out" in text
    assert "Your decision" not in text


def test_operator_blocker_surfaces_the_exact_question_without_templates(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=False, open_ended=False),
    )
    question = (
        "Grant macOS Accessibility permission to Terminal and Node, then "
        "confirm so I can rerun `npm run verify:macos-computer-use`."
    )

    supervisor._emit({
        "type": "life.mission.completed",
        "item_id": "task-accessibility",
        "title": "Verify macOS computer use",
        "success": False,
        "status": "paused_operator",
        "stop_reason": "Engineer requires an operator-owned decision.",
        "summary": "The harness builds, but UI automation is not yet verified.",
        "operator_question": question,
    })

    text = json.loads(
        (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )["text"]
    assert text == (
        "Verify macOS computer use\n"
        "Mission summary: The harness builds, but UI automation is not yet verified.\n"
        f"{question}"
    )
    assert "Reason:" not in text
    assert "Next:" not in text
    assert "Argus will diagnose" not in text


def test_final_submission_completion_is_explicitly_certified(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=False, open_ended=False),
    )

    supervisor._emit({
        "type": "life.mission.completed",
        "item_id": "task-4",
        "title": "Prepare final ICLR submission",
        "success": True,
        "status": "done",
        "final_submission_certified": True,
        "outcome": {
            "review_status": "done",
            "stage_certification": "certified",
        },
    })

    text = json.loads(
        (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )["text"]
    assert text == (
        "Submission certified · Prepare final ICLR submission · review=done\n"
        "The final submission passed independent review."
    )


def test_bounded_independent_review_completion_is_natural_and_footer_free(
    tmp_path,
) -> None:
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=False, open_ended=False),
    )
    append_turn(memory.root, "operator", "请实现缓存，并由独立 Reviewer 检查。")

    supervisor._emit({
        "type": "life.mission.completed",
        "item_id": "task-reviewed",
        "title": "Implement cache",
        "success": True,
        "status": "done",
        "summary": (
            "实现了线程安全缓存并通过 20 项测试。\n\n"
            "Decision:\n"
            "MILESTONE_STATUS=done\n"
            "RESULT=cache implementation passed\n"
            "NEXT_OWNER=reviewer"
        ),
        "independent_review_required": True,
        "overall_complete": True,
        "outcome": {
            "review_status": "done",
            "stage_certification": "deferred",
        },
    })

    text = json.loads(
        (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )["text"]
    assert text.startswith("任务已完成\n")
    assert "实现了线程安全缓存并通过 20 项测试。" in text
    assert "独立 Reviewer 已检查实现和测试，未发现阻断问题。" in text
    for internal in (
        "RESULT=",
        "NEXT_OWNER",
        "review=done",
        "阶段结论",
        "本计划工作项",
        "Implement cache",
    ):
        assert internal not in text
