"""Tests for canonical terminal event and command formatting."""

from __future__ import annotations

from argus_skill.cli.event_format import (
    annotate_progress_result,
    format_event_message,
    format_progress_command,
)


def test_unknown_event_keeps_bracketed_form() -> None:
    assert format_event_message({"type": "custom.event", "text": "x"}) == ("[custom.event] x")


def test_historical_alias_uses_canonical_loop_renderer() -> None:
    event = {
        "type": "loop.started",
        "objective": "build a CLI",
        "max_rounds": 3,
        "plan_mode": "auto",
    }
    rendered = format_event_message(event)
    assert rendered.startswith("🚀 task: build a CLI")
    assert "max_rounds=3" in rendered
    assert "plan_mode=auto" in rendered


def test_loop_start_hides_memory_prelude() -> None:
    rendered = format_event_message(
        {
            "type": "loop.start",
            "text": "### Memory context\nold data\n## Live objective\nship it",
        }
    )
    assert rendered == "🚀 task: ship it"
    assert "old data" not in rendered


def test_round_start_alias_and_canonical_share_renderer() -> None:
    expected = "🔁 round 3 starting…"
    assert format_event_message({"type": "round.start", "round_index": 3}) == expected
    assert format_event_message({"type": "round.started", "round_index": 3}) == expected


def test_round_main_completed_shows_output_or_fatal_error() -> None:
    complete = format_event_message(
        {
            "type": "round.main.completed",
            "round_index": 1,
            "last_message": "pytest: 6 passed",
            "turn_completed": True,
        }
    )
    assert "main agent finished" in complete
    assert "pytest: 6 passed" in complete

    failed = format_event_message(
        {
            "type": "round.main.completed",
            "round_index": 2,
            "turn_failed": True,
            "fatal_error": "operator stop",
        }
    )
    assert "turn_failed" in failed
    assert "operator stop" in failed


def test_round_review_completed_renders_verdict_and_next_action() -> None:
    done = format_event_message(
        {
            "type": "round.review.completed",
            "round_index": 1,
            "status": "done",
            "reason": "objective met",
            "next_action": "ignored",
        }
    )
    assert "✅ verified" in done
    assert "next:" not in done

    continued = format_event_message(
        {
            "type": "round.review.completed",
            "round_index": 2,
            "status": "continue",
            "reason": "tests failing",
            "next_action": "fix parser",
        }
    )
    assert "↻ more work is needed" in continued
    assert "fix parser" in continued


def test_loop_done_supports_structured_and_text_events() -> None:
    assert "FAILED" in format_event_message(
        {"type": "loop.completed", "success": False, "stop_reason": "budget"}
    )
    assert format_event_message({"type": "loop.done", "text": "review complete"}) == (
        "🏁 review complete"
    )


def test_life_mission_completed_leads_with_outcome_then_metrics() -> None:
    rendered = format_event_message(
        {
            "type": "life.mission.completed",
            "title": "Repair parser",
            "status": "done",
            "success": True,
            "summary": "Empty input now returns a clear error.",
            "rounds": 2,
            "elapsed_seconds": 3.25,
            "cost_usd": 0.5,
            "outcome": {
                "execution_status": "completed",
                "review_status": "done",
                "stage_certification": "not_certified",
                "interruption_kind": "none",
                "resumable": False,
            },
        }
    )
    assert "Completed: Repair parser. Empty input now returns a clear error." in rendered
    assert "2 rounds · 3.2s · cost $0.5000" in rendered
    assert "rounds=" not in rendered
    assert "execution=" not in rendered
    assert "review=" not in rendered


def test_match_info_diagnostic_still_renders() -> None:
    rendered = format_event_message({"type": "match.info", "text": "matched parser skill"})
    assert rendered == "🎯 matched parser skill"


def test_engineer_progress_redacts_raw_secret() -> None:
    secret = "ghp_" + "A" * 36
    rendered = format_event_message(
        {
            "type": "engineer.progress",
            "kind": "message",
            "text": f"using token {secret}",
        }
    )
    assert secret not in rendered
    assert "REDACTED" in rendered


def test_progress_command_unwraps_and_summarizes_shell() -> None:
    rendered = format_progress_command("/bin/bash -lc 'python -m pytest tests/test_x.py'")
    assert "pytest" in rendered
    assert "/bin/bash" not in rendered


def test_progress_result_marks_failure_and_keeps_excerpt() -> None:
    rendered = annotate_progress_result(
        "🧪 pytest tests/test_x.py",
        {"status": "failed", "exit_code": 1, "output_excerpt": "1 failed"},
    )
    assert rendered.startswith("❌ ")
    assert "1 failed" in rendered
