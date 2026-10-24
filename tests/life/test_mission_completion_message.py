"""Team completion must reach the operator without a follow-up question."""

from __future__ import annotations

from argus.core.transcript import append_turn
from argus.life.event_log import JsonlEventSink
from argus.life.memory import LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from tests.manager_inbox import team_reports as _reports


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

    transcript = _reports(tmp_path)
    assert len(transcript) == 1
    # A passed review is no label; the step is named once, by its title.
    assert transcript[0]["text"].startswith("One step done: Fix the first issue.\n")
    assert "Updated the parser and passed focused regression tests." in transcript[0]["text"]
    assert "review" not in transcript[0]["text"].lower()
    assert "the next step is being considered" in transcript[0]["text"]
    ui_events = _reports(tmp_path)
    assert len(ui_events) == 1
    assert ui_events[0]["summary"] == (
        "Updated the parser and passed focused regression tests."
    )
    assert ui_events[0]["campaign_continues"] is True
    assert ui_events[0]["delivery"] is None


def test_a_task_finished_while_another_waits_on_the_operator_is_only_that_item(tmp_path) -> None:
    """Trial s-934a4569: char_count finished while the truncate task waited on
    the operator's choice, and the message said the whole thing was done."""
    from argus.life.memory import BacklogItem, park_item

    memory = LifeMemory.open(tmp_path)
    asked = BacklogItem.new(title="strtools truncate 冲突需求裁决", objective="truncate 选 A 还是 B")
    asked.pending_question = "请选择 A 还是 B？"
    park_item(asked, "operator_question")
    memory.backlog.add(asked)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=True, open_ended=False),
    )
    append_turn(memory.root, "operator", "在 strtools/core.py 加 char_count。")
    event = {
        "type": "life.mission.completed", "item_id": "task-count", "title": "新增字符计数函数 char_count",
        "success": True, "status": "done", "summary": "char_count 已完整交付。",
        "independent_review_required": True, "review_source": "reviewer",
        "overall_complete": True, "campaign_continues": False,
        "outcome": {"review_status": "done", "stage_certification": "certified"},
    }

    supervisor._emit(event)

    text = _reports(tmp_path)[-1]["text"]
    # Trial s-36ca9747: it said "计划里的其他工作还在进行" while the other item
    # only waited on the operator's answer.
    assert text == (
        "这一项做完了，独立审阅也没发现要改的地方；另一项在等你回答。\n"
        "char_count 已完整交付。"
    )


def test_a_handoff_to_the_manager_says_the_reason_once_and_who_decides(tmp_path) -> None:
    """Trial s-6f0e6213: the Engineer handed a rounding conflict to the Manager;
    the message said its reason twice and "接下来：我先查清原因"."""
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=True, open_ended=False),
    )
    append_turn(memory.root, "operator", "给 timefmt 加一个 format_hours(seconds)。")
    reason = "暂未实现：现有舍入规则冲突。\n\n产品代码保持不变；现有测试通过。"
    supervisor._emit({
        "type": "life.mission.completed", "item_id": "task-hours", "title": "为 timefmt 添加整小时格式化函数",
        "success": False, "status": "parked", "park_reason": "research_open",
        "review_source": "engineer_handoff", "stop_reason": reason, "failure_reason": reason,
        "summary": " ".join(reason.split()), "operator_question": "",
        "outcome": {"review_status": "blocked", "execution_status": "paused"},
    })

    text = _reports(tmp_path)[-1]["text"]
    # Its first sentence is the reason; the rest is on the handoff's own row.
    assert text == (
        "「为 timefmt 添加整小时格式化函数」暂时做不下去了。\n"
        "原因：暂未实现：现有舍入规则冲突。\n"
        "接下来：经理在定怎么继续。"
    )


def _last_said(supervisor: LifeSupervisor, event: dict, root) -> str:
    supervisor._emit({"type": "life.mission.completed", **event})
    return _reports(root)[-1]["text"]


def test_a_review_handed_to_the_manager_says_the_manager_decides(tmp_path) -> None:
    """Trial s-757b9dac: the Reviewer's `→ manager` read "原因：<600 字> 接下来：<the same 600 字>"."""
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory, runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=True, open_ended=False),
    )
    append_turn(memory.root, "operator", "给 timefmt 加一个 format_hours(seconds)。")
    review = (
        "format_hours 尚未交付；现有整单位函数的舍入规则不一致，需要先明确小时函数应遵循哪一种规则。\n\n"
        "从包的公开接口执行 `from timefmt import format_hours` 直接触发 ImportError。\n\n"
        "请明确小时函数采用舍弃小数部分还是半数进一，再交由实现方补齐。"
    )

    assert _last_said(supervisor, {
        "item_id": "task-hours", "title": "为 timefmt 添加整小时格式化函数",
        "success": False, "status": "parked", "park_reason": "research_open", "review_source": "reviewer",
        "stop_reason": review, "failure_reason": review, "next_action": review,
        "summary": " ".join(review.split()), "operator_question": "",
        "outcome": {"review_status": "blocked", "execution_status": "paused"},
    }, memory.root) == (
        "「为 timefmt 添加整小时格式化函数」暂时做不下去了。\n"
        "原因：format_hours 尚未交付；现有整单位函数的舍入规则不一致，需要先明确小时函数应遵循哪一种规则。\n"
        "接下来：经理在定怎么继续。"
    )


def test_work_handed_back_to_the_planner_gives_the_engineer_s_reason(tmp_path) -> None:
    """Trial s-757b9dac: "原因：The mission ended without a verified result.
    接下来：我先查清原因", then the Engineer's whole summary."""
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory, runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=True, open_ended=False),
    )
    append_turn(memory.root, "operator", "给 timefmt 加一个 format_hours(seconds)。")

    assert _last_said(supervisor, {
        "item_id": "task-hours", "title": "为 timefmt 添加整小时格式化函数",
        "success": False, "status": "replan_requested", "review_source": "engineer_handoff",
        "stop_reason": "", "failure_reason": "", "next_action": "",
        "summary": "尚未实现：现有整单位函数的舍入规则不一致。未修改仓库文件，交给 Planner 明确舍入规则。",
        "outcome": {"review_status": "not_assessed", "execution_status": "ended"},
    }, memory.root) == (
        "「为 timefmt 添加整小时格式化函数」需要换个做法。\n"
        "原因：尚未实现：现有整单位函数的舍入规则不一致。\n"
        "接下来：规划者在重新安排。"
    )


def test_a_task_ended_without_any_account_says_no_reason(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory, runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=True, open_ended=False),
    )
    append_turn(memory.root, "operator", "给 timefmt 加一个 format_hours(seconds)。")

    assert _last_said(supervisor, {
        "item_id": "task-hours", "title": "为 timefmt 添加整小时格式化函数",
        "success": False, "status": "failed", "outcome": {},
    }, memory.root) == (
        "没能完成：为 timefmt 添加整小时格式化函数。\n"
        "接下来：我先查清原因，再找个能走通的办法。"
    )


def test_a_completion_after_an_option_click_keeps_the_operator_s_language(tmp_path) -> None:
    """Trial s-7d356ee9: the operator clicked 'B：format_date → …', the option's
    words became their latest turn, and the completion came out in English."""
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory, runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=False, open_ended=False),
    )
    append_turn(memory.root, "operator", "datefmt 的 format_date 收到两条互相冲突的需求，必须由我来拍板。")
    append_turn(memory.root, "operator", 'B：format_date → "22.10.2026"，format_month → "10.2026"')

    assert _last_said(supervisor, {
        "item_id": "task-date", "title": "Switch format_date to day.month.year",
        "success": True, "status": "done", "summary": "datefmt 已实现你选定的 B 方案。",
        "independent_review_required": True, "review_source": "reviewer", "overall_complete": True,
        "outcome": {"review_status": "done", "stage_certification": "certified"},
        "delivery": {"delivery_id": "d-date", "primary_target": {"label": "core.py", "path": "datefmt/core.py"}},
    }, memory.root) == (
        "做完了，独立审阅也没发现要改的地方。\n"
        "datefmt 已实现你选定的 B 方案。\n"
        "结果在 datefmt/core.py。"
    )


def test_a_step_of_a_continuing_project_is_named_as_a_step(tmp_path) -> None:
    """Trial s-6f0e6213 read "做完一步：实现并独立审阅整小时格式化功能"."""
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=True, continuous_objective="timefmt", open_ended=True),
    )
    append_turn(memory.root, "operator", "给 timefmt 加一个 format_hours(seconds)。")
    supervisor._emit({
        "type": "life.mission.completed", "item_id": "task-hours", "title": "实现并独立审阅整小时格式化功能",
        "success": True, "status": "done", "summary": "format_hours 已通过独立审阅。",
        "outcome": {"review_status": "done"},
    })

    text = _reports(tmp_path)[-1]["text"]
    assert text.splitlines()[0] == "「实现并独立审阅整小时格式化功能」这一步做完了。"
