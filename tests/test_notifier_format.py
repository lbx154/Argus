"""Tests for ``cli.event_format`` — pure event-string rendering.

Provenance: split out from the Telegram-era ``test_notifier_format.py``
when the Telegram integration was retired. Only the pure formatter
helpers remain; tests for ``TelegramNotifier`` / verbose-toggle were
deleted along with that subsystem.
"""
from __future__ import annotations

from argus_skill.cli.event_format import format_event_message


def test_format_known_event_uses_icon_and_drops_brackets() -> None:
    msg = format_event_message({"type": "task.completed", "text": "all good"})
    assert msg.startswith("✅ ")
    assert "[task.completed]" not in msg
    assert "all good" in msg


def test_format_unknown_event_keeps_legacy_bracketed_form() -> None:
    # ``round.start`` used to be in this category — it now has an icon
    # and a rich renderer because life-mode surfaces it directly. Use a
    # truly unknown kind to exercise the legacy fallback path.
    msg = format_event_message({"type": "totally.fictional.kind", "text": "x"})
    assert msg == "[totally.fictional.kind] x"


def test_format_no_text_emits_just_icon_for_known_kind() -> None:
    msg = format_event_message({"type": "daemon.started", "text": ""})
    assert msg == "🟢"


def test_format_task_completed_allows_long_payload() -> None:
    long_answer = "x" * 1800
    msg = format_event_message({"type": "task.completed", "text": long_answer})
    # Capped at 1500 chars, so plus icon + space + ellipsis ≈ 1503ish.
    assert len(msg) <= 1505
    assert msg.endswith("…")
    assert msg.startswith("✅ ")


def test_format_short_event_caps_at_300_for_non_completion() -> None:
    long_text = "x" * 500
    msg = format_event_message({"type": "task.started", "text": long_text})
    assert len(msg) <= 305
    assert msg.endswith("…")
    assert msg.startswith("🏃 ")


# ---------------------------------------------------------------------------
# Rich payload renderers (LoopEngine + SkillLoopRunner mission events)
# ---------------------------------------------------------------------------


def test_format_loop_started_shows_objective_and_max_rounds() -> None:
    msg = format_event_message({
        "type": "loop.started",
        "objective": "build a CLI",
        "max_rounds": 500,
        "plan_mode": "auto",
    })
    assert msg.startswith("🚀 ")
    assert "max_rounds=500" in msg
    assert "plan_mode=auto" in msg
    assert "build a CLI" in msg


def test_format_round_started_shows_round_index() -> None:
    msg = format_event_message({"type": "round.started", "round_index": 3})
    assert msg == "🔁 round 3 starting…"


def test_format_round_main_completed_shows_last_message() -> None:
    msg = format_event_message({
        "type": "round.main.completed",
        "round_index": 1,
        "turn_completed": True,
        "turn_failed": False,
        "last_message": "wrote todo.py and ran pytest: 6 passed",
    })
    assert msg.startswith("🔧 round 1: main agent finished")
    assert "wrote todo.py and ran pytest: 6 passed" in msg


def test_format_round_main_completed_shows_fatal_when_no_message() -> None:
    msg = format_event_message({
        "type": "round.main.completed",
        "round_index": 2,
        "turn_completed": False,
        "turn_failed": True,
        "fatal_error": "External interrupt: operator stop",
        "last_message": "",
    })
    assert "turn_failed" in msg
    assert "External interrupt" in msg


def test_format_round_checks_completed_summarises_pass_fail() -> None:
    msg = format_event_message({
        "type": "round.checks.completed",
        "round_index": 1,
        "checks": [
            {"command": "pytest -q", "exit_code": 0, "passed": True},
            {"command": "ruff check .", "exit_code": 1, "passed": False},
        ],
    })
    assert "1 ✓ / 1 ✗" in msg
    assert "ruff check" in msg
    assert "exit 1" in msg


def test_format_round_checks_completed_handles_no_checks() -> None:
    msg = format_event_message({
        "type": "round.checks.completed",
        "round_index": 1,
        "checks": [],
    })
    assert "no acceptance checks" in msg


def test_format_round_review_completed_done() -> None:
    msg = format_event_message({
        "type": "round.review.completed",
        "round_index": 1,
        "status": "done",
        "reason": "objective met",
        "next_action": "",
    })
    assert "✅ done" in msg
    assert "objective met" in msg
    # 'done' status doesn't show next-action even if present.
    assert "next:" not in msg


def test_format_round_review_completed_continue_shows_next() -> None:
    msg = format_event_message({
        "type": "round.review.completed",
        "round_index": 2,
        "status": "continue",
        "reason": "tests still failing",
        "next_action": "fix the rm error path",
    })
    assert "↻ continue" in msg
    assert "tests still failing" in msg
    assert "fix the rm error path" in msg


def test_format_plan_completed_shows_main_and_explore() -> None:
    msg = format_event_message({
        "type": "plan.completed",
        "round_index": 3,
        "plan_mode": "auto",
        "follow_up_required": True,
        "main_instruction": "add --json flag",
        "next_explore": "test the new flag",
        "review_instruction": "verify json output",
    })
    assert "round 3 plan (auto)" in msg
    assert "follow-up needed" in msg
    assert "add --json flag" in msg
    assert "test the new flag" in msg
    assert "verify json output" in msg


def test_format_plan_completed_no_followup() -> None:
    msg = format_event_message({
        "type": "plan.completed",
        "round_index": 5,
        "plan_mode": "auto",
        "follow_up_required": False,
        "main_instruction": "",
        "next_explore": "",
        "review_instruction": "",
    })
    assert "no more follow-up" in msg


def test_format_round_control_injected_shows_text() -> None:
    msg = format_event_message({
        "type": "round.control.injected",
        "round_index": 4,
        "instruction": "switch to JSON output",
    })
    assert msg.startswith("💉 round 4")
    assert "switch to JSON output" in msg


def test_format_loop_completed_success() -> None:
    msg = format_event_message({
        "type": "loop.completed",
        "success": True,
        "stop_reason": "objective met cleanly",
    })
    assert "success" in msg
    assert "objective met cleanly" in msg


def test_format_loop_completed_failure() -> None:
    msg = format_event_message({
        "type": "loop.completed",
        "success": False,
        "stop_reason": "max_rounds exceeded",
    })
    assert "FAILED" in msg
    assert "max_rounds exceeded" in msg


def test_format_final_report_ready_shows_path() -> None:
    msg = format_event_message({
        "type": "final.report.ready",
        "path": "/tmp/x/final.md",
        "generated_by": "main-agent",
    })
    assert "/tmp/x/final.md" in msg
    assert "main-agent" in msg


def test_format_command_ack_show_kind_wraps_in_fence() -> None:
    out = format_event_message({
        "type": "command.ack",
        "text": "round 1 prompt body\nmore lines",
        "show_kind": "prompt",
    })
    assert "/show prompt" in out
    assert "```" in out
    assert "round 1 prompt body" in out
    assert "more lines" in out


def test_format_command_ack_plain_text_unchanged() -> None:
    out = format_event_message({
        "type": "command.ack",
        "text": "plan_mode → auto",
    })
    assert "```" not in out  # plain ack — no fence
    assert "plan_mode → auto" in out


def test_format_engineer_progress_redacts_raw_secret() -> None:
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345678901"
    out = format_event_message({
        "type": "engineer.progress",
        "kind": "message",
        "text": f"using token {secret}",
    })
    assert secret not in out
    assert "REDACTED" in out


def test_format_status_report_preserves_multi_line() -> None:
    body = (
        "mission X running   round 3/5   phase=review\n"
        "   objective: do the thing\n"
        "   plan_mode: auto\n"
        "   last review (round 2): ↻ continue — tests failing\n"
    )
    out = format_event_message({"type": "status.report", "text": body})
    # Both the icon and every body line must survive — no 300-char chop.
    assert "📊" in out
    assert "round 3/5" in out
    assert "↻ continue" in out
    assert "tests failing" in out
