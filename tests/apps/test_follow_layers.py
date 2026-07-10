from argus_skill.apps.cli._follow import (
    _follow_layer_from_event,
    _format_follow_command,
)


def test_follow_layer_detects_all_four_roles() -> None:
    assert _follow_layer_from_event({"type": "life.manager.intent.completed"}, "engineer") == "manager"
    assert _follow_layer_from_event({"type": "life.planner.verdict"}, "engineer") == "planner"
    assert _follow_layer_from_event({"type": "round.start"}, "planner") == "engineer"
    assert _follow_layer_from_event({"type": "round.review.started"}, "engineer") == "reviewer"


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
