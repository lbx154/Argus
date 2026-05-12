"""Tests for terminal-side event rendering (cli/render.py).

We use ``Theme(enabled=False)`` so output is plain text — easier to
assert on, and matches what unit-test environments actually see.
"""

from __future__ import annotations

from argus_skill.cli.render import render_event_for_terminal, render_welcome_banner
from argus_skill.cli.theme import BOX, Theme

_PLAIN = Theme(enabled=False, width=80)
_ANSI = Theme(enabled=True, width=80)


# ── round dividers ────────────────────────────────────────────────────────

def test_round_started_prepends_horizontal_rule() -> None:
    e = {"type": "round.started", "round_index": 3,
         "text": "round 3 starting…"}
    out = render_event_for_terminal(e, theme=_PLAIN)
    assert out.startswith("\n")
    assert "Round 3" in out
    assert BOX["h"] in out


def test_round_started_unknown_index_renders_question_mark() -> None:
    e = {"type": "round.started", "text": "..."}
    out = render_event_for_terminal(e, theme=_PLAIN)
    assert "Round ?" in out


# ── mission lifecycle ─────────────────────────────────────────────────────

def test_mission_completed_success_uses_color_when_enabled() -> None:
    e = {
        "type": "mission.completed",
        "text": "mission abc-123: success=True rounds=2 reason=ok",
    }
    plain = render_event_for_terminal(e, theme=_PLAIN)
    coloured = render_event_for_terminal(e, theme=_ANSI)
    # plain output is identical to the bare formatted message
    assert plain.strip().endswith("success=True rounds=2 reason=ok") or \
           "success=True" in plain
    # coloured output contains an ANSI bold-green prefix on the first line
    assert "\x1b[" in coloured


def test_mission_completed_failure_is_red_when_enabled() -> None:
    e = {
        "type": "mission.completed",
        "text": "mission abc-123: success=False rounds=5 reason=hit_max_rounds",
    }
    coloured = render_event_for_terminal(e, theme=_ANSI)
    # bold red sequence — \x1b[1m\x1b[31m
    assert "\x1b[1m\x1b[31m" in coloured


def test_mission_idle_renders_in_left_box() -> None:
    e = {
        "type": "mission.idle",
        "text": "mission idle — daemon is alive; type /run, /show, or /exit",
    }
    out = render_event_for_terminal(e, theme=_PLAIN)
    # left-box decoration:
    assert out.startswith(BOX["left_top"])
    assert "mission idle" in out
    assert out.rstrip().endswith(BOX["h"])


# ── status.report ─────────────────────────────────────────────────────────

def test_status_report_uses_left_box() -> None:
    body = (
        "mission abc-123   running   round 2/10   phase=engineering\n"
        "   objective: build FizzBuzz CLI\n"
        "   plan_mode: auto\n"
        "   last review: ✅ done — both checks passed\n"
        "   recent:\n"
        "     12:34:56 round 2 started\n"
        "     12:35:01 main agent finished"
    )
    e = {"type": "status.report", "text": body}
    out = render_event_for_terminal(e, theme=_PLAIN)
    lines = out.splitlines()
    assert lines[0].startswith(BOX["left_top"])
    assert "mission abc-123" in lines[0]
    # objective key appears as "objective: ..." in body
    assert any("objective" in ln for ln in lines)
    # recent label has its own divider line
    assert any(ln.startswith(BOX["v"]) and "recent" in ln for ln in lines)
    # The recent timestamp survives.
    assert any("12:34:56" in ln for ln in lines)
    # Footer is the └─ line.
    assert lines[-1].startswith(BOX["left_bot"])


def test_status_report_empty_returns_short_string() -> None:
    e = {"type": "status.report", "text": ""}
    out = render_event_for_terminal(e, theme=_PLAIN)
    assert "no status" in out


# ── /show responses ──────────────────────────────────────────────────────

def test_show_ack_uses_left_box_with_show_kind_in_title() -> None:
    e = {
        "type": "command.ack",
        "show_kind": "review",
        "text": "── review_round_2 ──\nverdict: done\nreason: …",
    }
    out = render_event_for_terminal(e, theme=_PLAIN)
    assert out.startswith(BOX["left_top"])
    assert "/show review" in out
    assert "review_round_2" in out
    assert "verdict: done" in out


def test_show_ack_empty_renders_empty_marker() -> None:
    e = {"type": "command.ack", "show_kind": "plan", "text": ""}
    out = render_event_for_terminal(e, theme=_PLAIN)
    assert "(empty)" in out
    assert "/show plan" in out


# ── plain ack falls through ──────────────────────────────────────────────

def test_plain_command_ack_does_not_use_left_box() -> None:
    e = {
        "type": "command.ack",
        "text": "verbose mode on — internal events will appear",
    }
    out = render_event_for_terminal(e, theme=_PLAIN)
    assert not out.startswith(BOX["left_top"])
    assert "verbose mode on" in out


# ── review verdict colors ────────────────────────────────────────────────

def test_review_done_colored_bold_green() -> None:
    e = {
        "type": "round.review.completed",
        "round_index": 2,
        "status": "done",
        "reason": "all checks passed",
    }
    coloured = render_event_for_terminal(e, theme=_ANSI)
    assert "\x1b[1m\x1b[32m" in coloured  # bold + green
    assert "✅" in coloured


def test_review_continue_colored_yellow() -> None:
    e = {
        "type": "round.review.completed",
        "round_index": 2,
        "status": "continue",
        "reason": "needs another round",
    }
    coloured = render_event_for_terminal(e, theme=_ANSI)
    assert "\x1b[33m" in coloured
    assert "↻" in coloured


def test_review_blocked_colored_bold_red() -> None:
    e = {
        "type": "round.review.completed",
        "round_index": 3,
        "status": "blocked",
        "reason": "stuck",
    }
    coloured = render_event_for_terminal(e, theme=_ANSI)
    assert "\x1b[1m\x1b[31m" in coloured
    assert "⛔" in coloured


# ── color disabled = plain passthrough ───────────────────────────────────

def test_disabled_theme_yields_no_ansi_codes() -> None:
    e = {"type": "round.main.completed", "round_index": 1,
         "text": "round 1: main agent finished"}
    plain = render_event_for_terminal(e, theme=_PLAIN)
    assert "\x1b" not in plain


# ── welcome banner ───────────────────────────────────────────────────────

def test_welcome_banner_is_a_full_box() -> None:
    out = render_welcome_banner(theme=_PLAIN)
    lines = out.splitlines()
    assert lines[0].startswith(BOX["tl"])
    assert lines[0].endswith(BOX["tr"])
    assert lines[-1].startswith(BOX["bl"])
    assert lines[-1].endswith(BOX["br"])
    assert any("/status" in ln for ln in lines)
    assert any("/show prompt|plan|review" in ln for ln in lines)
    assert any("/exit" in ln for ln in lines)


def test_welcome_banner_no_ansi_when_disabled() -> None:
    out = render_welcome_banner(theme=_PLAIN)
    assert "\x1b" not in out
