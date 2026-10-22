"""Team completion must reach the operator without a follow-up question."""

from __future__ import annotations

import json

from argus.core.manuscript_snapshot import manuscript_snapshot
from argus.core.transcript import append_turn
from argus.life.event_log import JsonlEventSink
from argus.life.memory import LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig


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
    # A passed review is no label; the step is named once, by its title.
    assert transcript[0]["text"].startswith("One step done: Fix the first issue.\n")
    assert "Updated the parser and passed focused regression tests." in transcript[0]["text"]
    assert "review" not in transcript[0]["text"].lower()
    assert "the next step is being considered" in transcript[0]["text"]
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
    assert text == "Done."


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
    assert text == "做完了。\n修复了解析器并通过回归测试。"
    assert "本次完成" not in text and "Mission summary" not in text


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
    assert text == "Done."


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
    assert "The large row timed out; smaller rows remain valid." in text
    assert "Mission summary" not in text
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
        f"Verify macOS computer use needs your call: {question}\n"
        "The harness builds, but UI automation is not yet verified."
    )
    assert "Reason:" not in text
    assert "Next:" not in text
    assert "Argus will diagnose" not in text


def test_final_submission_completion_is_explicitly_certified(tmp_path) -> None:
    manuscript = tmp_path / "paper/main.tex"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("final paper\n", encoding="utf-8")
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
        "execution_workdir": str(tmp_path),
        "manuscript_snapshot": manuscript_snapshot(tmp_path),
        "outcome": {
            "review_status": "done",
            "stage_certification": "certified",
        },
    })

    text = json.loads(
        (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )["text"]
    assert text == "The final submission passed independent review and is ready."


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
    append_turn(memory.root, "operator", "请调研缓存方案，并由独立 Reviewer 检查。")

    supervisor._emit({
        "type": "life.mission.completed",
        "item_id": "task-reviewed",
        "title": "Research cache options",
        "success": True,
        "status": "done",
        "summary": (
            "完成了缓存方案调研并形成报告。\n\n"
            "Decision:\n"
            "RESULT=cache research reviewed\n"
            "→ done"
        ),
        "independent_review_required": True,
        "review_source": "reviewer",
        "overall_complete": True,
        "outcome": {
            "review_status": "done",
            "stage_certification": "deferred",
        },
    })

    text = json.loads(
        (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )["text"]
    # One headline says it was done, checked, and that other planned work goes on.
    assert text.startswith(
        "这一项做完了，独立审阅也没发现要改的地方；计划里的其他工作还在进行。\n"
    )
    assert "完成了缓存方案调研并形成报告。" in text
    assert "Reviewer" not in text
    assert "实现和测试" not in text
    for internal in (
        "RESULT=",
        "→",
        "review=done",
        "阶段结论",
        "本计划工作项",
        "Research cache options",
    ):
        assert internal not in text


def test_a_reviewed_completion_gives_the_review_s_first_sentence_and_the_result(tmp_path) -> None:
    """2026-10-21: two completion messages repeated the Reviewer's whole
    first-person review, test command and code-point counts included, right
    under the review row that had just shown it."""
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=False, open_ended=False),
    )
    append_turn(memory.root, "operator", "在 strtools/core.py 加 char_count。")
    review = (
        "`char_count` 已完整交付；我独立确认了默认计数和包级导出。 "
        "我重新运行 `python -m pytest -q`，176 项全部通过。"
    )

    supervisor._emit({
        "type": "life.mission.completed", "item_id": "task-count", "title": "新增字符计数函数 char_count",
        "success": True, "status": "done", "summary": review,
        "independent_review_required": True, "review_source": "reviewer", "overall_complete": True,
        "outcome": {"review_status": "done", "stage_certification": "certified"},
        "delivery": {"delivery_id": "d-1", "primary_target": {"label": "core.py", "path": "strtools/core.py"}},
    })

    text = json.loads(
        (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )["text"]
    assert text == (
        "做完了，独立审阅也没发现要改的地方。\n"
        "`char_count` 已完整交付；我独立确认了默认计数和包级导出。\n"
        "结果在 strtools/core.py。"
    )


def test_continued_mission_names_the_next_queued_task(tmp_path) -> None:
    from argus.life.memory import BacklogItem

    memory = LifeMemory.open(tmp_path)
    memory.backlog.add(BacklogItem.new(title="Write the release notes", objective="notes"))
    memory.backlog.add(BacklogItem.new(title="Record the demo video", objective="video"))
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
        "summary": "Updated the parser.",
        "outcome": {"review_status": "done"},
    }

    assert supervisor._emit(event) is True

    transcript = [
        json.loads(line)
        for line in (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(transcript) == 1
    assert (
        "Next up: Write the release notes, with one more queued after it."
        in transcript[0]["text"]
    )
    assert "Planner is selecting" not in transcript[0]["text"]


def test_a_self_review_is_never_announced_as_an_independent_one(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=False, open_ended=False),
    )
    append_turn(memory.root, "operator", "请实现折行函数。")

    supervisor._emit({
        "type": "life.mission.completed", "item_id": "task-self", "title": "Wrap text",
        "success": True, "status": "done", "summary": "实现了折行函数。",
        "independent_review_required": True, "review_source": "engineer_self_review",
        "overall_complete": True, "outcome": {"review_status": "done"},
    })

    text = json.loads(
        (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )["text"]
    assert "独立审阅者" not in text
