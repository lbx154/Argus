"""Tests for the active headless terminal event renderer."""

from __future__ import annotations

from argus_skill.cli.render import render_event_for_terminal
from argus_skill.cli.theme import BOX, Theme

_PLAIN = Theme(enabled=False, width=80)
_ANSI = Theme(enabled=True, width=80)


def test_canonical_round_start_prepends_horizontal_rule() -> None:
    out = render_event_for_terminal(
        {"type": "round.start", "round_index": 3},
        theme=_PLAIN,
    )
    assert out.startswith("\n")
    assert "Round 3" in out
    assert BOX["h"] in out


def test_historical_round_alias_uses_same_rule() -> None:
    out = render_event_for_terminal(
        {"type": "round.started", "round_index": 2},
        theme=_PLAIN,
    )
    assert "Round 2" in out


def test_canonical_mission_start_has_rule_and_title() -> None:
    out = render_event_for_terminal(
        {"type": "life.mission.started", "title": "ship the cockpit"},
        theme=_PLAIN,
    )
    assert "Mission" in out
    assert "ship the cockpit" in out


def test_mission_completion_color_uses_structured_status() -> None:
    success = render_event_for_terminal(
        {"type": "life.mission.completed", "status": "done"},
        theme=_ANSI,
    )
    failure = render_event_for_terminal(
        {"type": "life.mission.completed", "status": "error"},
        theme=_ANSI,
    )
    assert "\x1b[1m\x1b[32m" in success
    assert "\x1b[1m\x1b[31m" in failure


def test_life_mission_completed_renders_a_human_outcome() -> None:
    rendered = render_event_for_terminal(
        {
            "type": "life.mission.completed",
            "status": "done",
            "outcome": {
                "execution_status": "completed",
                "review_status": "done",
                "stage_certification": "not_certified",
                "interruption_kind": "none",
                "resumable": False,
            },
        },
        theme=_PLAIN,
    )
    assert "Completed: current task." in rendered
    assert "execution=" not in rendered
    assert "review=" not in rendered
    assert "stage=" not in rendered


def test_engineer_progress_terminal_redacts_raw_secret() -> None:
    secret = "ghp_" + "A" * 36
    rendered = render_event_for_terminal(
        {
            "type": "engineer.progress",
            "kind": "command_execution",
            "text": f"curl -H 'Authorization: token {secret}' example.com",
        },
        theme=_PLAIN,
    )
    assert secret not in rendered
    assert "REDACTED" in rendered


def test_engineer_reasoning_is_hidden_by_default() -> None:
    rendered = render_event_for_terminal(
        {"type": "engineer.progress", "kind": "reasoning", "text": "private"},
        theme=_PLAIN,
    )
    assert rendered == ""


def test_engineer_handoff_fields_are_hidden() -> None:
    rendered = render_event_for_terminal(
        {
            "type": "engineer.progress",
            "kind": "assistant_message",
            "text": (
                "Artifact complete.\n"
                "MILESTONE_STATUS=done\n"
                "NEXT_OWNER=reviewer\n"
                "OPERATOR_QUESTION=none\n"
                "OPERATOR_OPTIONS=none"
            ),
        },
        theme=_PLAIN,
    )
    assert "Artifact complete." in rendered
    assert "NEXT_OWNER" not in rendered
    assert "OPERATOR_" not in rendered


def test_review_verdict_colors() -> None:
    done = render_event_for_terminal(
        {
            "type": "round.review.completed",
            "round_index": 2,
            "status": "done",
            "reason": "all checks passed",
        },
        theme=_ANSI,
    )
    continued = render_event_for_terminal(
        {
            "type": "round.review.completed",
            "round_index": 3,
            "status": "continue",
            "reason": "needs work",
        },
        theme=_ANSI,
    )
    blocked = render_event_for_terminal(
        {
            "type": "round.review.completed",
            "round_index": 4,
            "status": "blocked",
            "reason": "external dependency",
        },
        theme=_ANSI,
    )
    assert "\x1b[1m\x1b[32m" in done
    assert "\x1b[33m" in continued
    assert "\x1b[1m\x1b[31m" in blocked


def test_disabled_theme_yields_no_ansi_codes() -> None:
    rendered = render_event_for_terminal(
        {"type": "round.main.completed", "round_index": 1},
        theme=_PLAIN,
    )
    assert "\x1b" not in rendered
