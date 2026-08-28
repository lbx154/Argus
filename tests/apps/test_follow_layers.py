import json

from argus_skill.apps.cli._follow import (
    _follow_layer_from_event,
    _format_follow_command,
    _format_follow_event,
    _read_recent_project_events,
)


def test_follow_routing_failure_keeps_raw_diagnostics_out_of_headline() -> None:
    rendered = _format_follow_event(
        {
            "type": "life.manager.intent.failed",
            "phase": "backend",
            "cause": "401 Missing bearer",
            "attempts": 2,
            "error": "VerticalDecisionError: raw failure",
            "objective": "route this request",
        },
        "manager",
    )

    assert rendered is not None
    assert "I couldn’t determine how to handle this request" in rendered
    assert "Nothing was queued" in rendered
    assert "401 Missing bearer" not in rendered
    assert "VerticalDecisionError" not in rendered


def test_follow_planner_and_stage_messages_hide_storage_fields_and_enums() -> None:
    added = _format_follow_event(
        {
            "type": "life.planner.task_added",
            "item_id": "task-secret",
            "title": "Verify the result",
            "objective": "Run the acceptance test",
        },
        "planner",
    )
    skipped = _format_follow_event(
        {
            "type": "life.planner.task_skipped",
            "title": "Repeat the benchmark",
            "matched_item_id": "task-secret",
            "matched_title": "Benchmark the candidate",
            "matched_status": "done",
            "skip_category": "duplicate",
            "reason": "The existing task has the same acceptance check.",
        },
        "planner",
    )
    stage = _format_follow_event(
        {
            "type": "life.manager.stage_decision",
            "action": "advance",
            "target_stage": "final_submission",
            "reason": "The required checks passed.",
        },
        "manager",
    )

    assert added == "📋 [Planner] Planner added “Verify the result”: Run the acceptance test"
    assert "task-secret" not in added
    assert skipped is not None and "already covers it" in skipped
    assert "matched_" not in skipped and "skip_category" not in skipped
    assert stage == "🧭 [Manager] Advanced to final submission: The required checks passed."
    assert "advance" not in stage and "_" not in stage


def test_follow_round_and_completion_messages_read_as_outcomes() -> None:
    review = _format_follow_event(
        {
            "type": "round.review.completed",
            "round_index": 2,
            "status": "continue",
            "reason": "One edge case remains.",
        },
        "reviewer",
    )
    completed = _format_follow_event(
        {
            "type": "life.mission.completed",
            "item_id": "task-secret",
            "title": "Repair the parser",
            "objective": "Handle empty input",
            "status": "done",
            "success": True,
            "summary": "The regression test passes.",
        },
        "engineer",
    )

    assert review == "✅ [Reviewer] Requested another pass after round 2. One edge case remains."
    assert "status=" not in review and "round=" not in review
    assert completed == "✅ Completed: Repair the parser. The regression test passes."
    assert "task-secret" not in completed and "success=" not in completed


def test_follow_layer_detects_all_four_roles() -> None:
    assert _follow_layer_from_event({"type": "life.manager.intent.completed"}, "engineer") == "manager"
    assert _follow_layer_from_event({"type": "life.planner.verdict"}, "engineer") == "planner"
    assert _follow_layer_from_event({"type": "round.start"}, "planner") == "engineer"
    assert _follow_layer_from_event({"type": "round.review.started"}, "engineer") == "reviewer"
    assert _follow_layer_from_event({"type": "round.review.deferred"}, "reviewer") == "engineer"


def test_follow_layer_prefers_explicit_agent_layer() -> None:
    assert _follow_layer_from_event({"agent_layer": "manager", "type": "round.start"}, "engineer") == "manager"


def test_follow_command_keeps_compact_file_read_format() -> None:
    rendered = _format_follow_command({
        "text": "cat /home/user/project/src/main.py",
        "status": "completed",
        "exit_code": 0,
    })
    assert rendered == "✅ 📖 读取 src/main.py"


def test_follow_command_summarizes_chains_and_failures() -> None:
    rendered = _format_follow_command({
        "text": "ruff check . && pytest -q",
        "status": "failed",
        "exit_code": 1,
        "output_excerpt": "tests failed",
    })
    assert "❌ 🔧 执行 2 步" in rendered
    assert "tests failed" in rendered


def test_recent_events_fill_from_rollover_when_live_log_is_short(tmp_path) -> None:
    previous = [{"type": "event", "seq": index} for index in range(1, 5)]
    current = [{"type": "event", "seq": index} for index in range(5, 7)]
    (tmp_path / "events.jsonl.1").write_text(
        "".join(json.dumps(row) + "\n" for row in previous),
        encoding="utf-8",
    )
    (tmp_path / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in current),
        encoding="utf-8",
    )

    rows = _read_recent_project_events(tmp_path, limit=4)

    assert [row["seq"] for row in rows] == [3, 4, 5, 6]


def test_recent_events_remove_exact_rollover_boundary_overlap(tmp_path) -> None:
    (tmp_path / "events.jsonl.1").write_text(
        "".join(json.dumps({"type": "event", "seq": seq}) + "\n" for seq in (1, 2, 3)),
        encoding="utf-8",
    )
    (tmp_path / "events.jsonl").write_text(
        "".join(json.dumps({"type": "event", "seq": seq}) + "\n" for seq in (2, 3, 4)),
        encoding="utf-8",
    )

    rows = _read_recent_project_events(tmp_path, limit=4)

    assert [row["seq"] for row in rows] == [1, 2, 3, 4]
