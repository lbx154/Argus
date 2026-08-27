import json

from argus_skill.apps.cli._follow import (
    _follow_layer_from_event,
    _format_follow_command,
    _format_follow_event,
    _read_recent_project_events,
)


def test_follow_routing_failure_leads_with_structured_facts() -> None:
    rendered = _format_follow_event(
        {
            "type": "life.manager.intent.failed",
            "phase": "backend",
            "cause": "401 Missing bearer",
            "attempts": 2,
            "error": "VerticalDecisionError: raw failure",
        },
        "manager",
    )

    assert rendered is not None
    assert "分流失败 · 后端 401 Missing bearer (第2次尝试)" in rendered
    assert "原始错误: VerticalDecisionError: raw failure" in rendered


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
