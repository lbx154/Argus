"""Tests for terminal-side event rendering (cli/render.py).

We use ``Theme(enabled=False)`` so output is plain text — easier to
assert on, and matches what unit-test environments actually see.
"""

from __future__ import annotations

from argus_skill.cli.render import render_event_for_terminal
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


def test_life_mission_completed_renders_canonical_outcome_dimensions() -> None:
    event = {
        "type": "life.mission.completed",
        "status": "done",
        "outcome": {
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "not_certified",
            "scientific_decision": "no_go",
            "failure_source": "",
            "interruption_kind": "none",
            "resumable": False,
        },
    }

    rendered = render_event_for_terminal(event, theme=_PLAIN)

    assert "execution=completed" in rendered
    assert "review=done" in rendered
    assert "stage=not_certified" in rendered
    assert "science=no_go" in rendered


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


# ── diff colouring in /show ────────────────────────────────────────────────

def test_show_ack_colorizes_unified_diff_add_remove() -> None:
    from argus_skill.cli.render import _render_show_ack
    text = (
        "diff --git a/x.py b/x.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-old = 1\n"
        "+new = 2\n"
        " unchanged\n"
    )
    out = _render_show_ack({"show_kind": "review", "text": text}, theme=_ANSI)
    # added line green (32 / mocha green), removed line red (31 / mocha red)
    assert ("\x1b[32m" in out) or ("38;2;166;227;161" in out)  # green add
    assert ("\x1b[31m" in out) or ("38;2;243;139;168" in out)  # red remove


def test_show_ack_leaves_prose_bullets_uncolored() -> None:
    # A plain review/plan (no diff headers) must NOT tint "- item" bullets.
    from argus_skill.cli.render import _render_show_ack
    text = "Plan:\n- step one\n+ a note\n"
    out = _render_show_ack({"show_kind": "plan", "text": text}, theme=_ANSI)
    # the '-'/'+' prose lines carry no red/green SGR wrapper
    assert "\x1b[31m" not in out and "38;2;243;139;168m-" not in out
    assert "\x1b[32m" not in out and "38;2;166;227;161m+" not in out


def test_show_ack_diff_plain_when_theme_disabled() -> None:
    from argus_skill.cli.render import _render_show_ack
    text = "@@ -1 +1 @@\n-a\n+b\n"
    out = _render_show_ack({"show_kind": "review", "text": text}, theme=_PLAIN)
    assert "\x1b" not in out
    assert "-a" in out and "+b" in out
