"""Tests for the argus-skill brand assets (logo + startup banner)."""

from __future__ import annotations

from argus_skill.cli.branding import (
    LOGO_COMPACT,
    LOGO_FULL,
    TAGLINE,
    render_logo,
    render_startup_banner,
)
from argus_skill.cli.theme import Theme


_PLAIN = Theme(enabled=False, width=100)


# ── logo ─────────────────────────────────────────────────────────────────

def test_logo_full_is_six_rows() -> None:
    rows = [r for r in LOGO_FULL.splitlines() if r.strip()]
    assert len(rows) == 6


def test_logo_compact_is_smaller() -> None:
    full_w = max(len(r) for r in LOGO_FULL.splitlines())
    compact_w = max(len(r) for r in LOGO_COMPACT.splitlines() if r.strip())
    assert compact_w < full_w


def test_render_logo_uses_full_when_term_is_wide() -> None:
    t = Theme(enabled=False, width=120)
    out = render_logo(theme=t)
    assert "█" in out  # full logo uses block characters


def test_render_logo_uses_compact_when_term_is_narrow() -> None:
    t = Theme(enabled=False, width=60)
    out = render_logo(theme=t)
    assert "█" not in out
    # compact logo has these underscore-based glyphs
    assert "_" in out or "/" in out


def test_render_logo_no_ansi_when_disabled() -> None:
    t = Theme(enabled=False, width=120)
    out = render_logo(theme=t)
    assert "\x1b" not in out


def test_render_logo_has_ansi_when_enabled() -> None:
    t = Theme(enabled=True, width=120)
    out = render_logo(theme=t)
    assert "\x1b[" in out


# ── startup banner ───────────────────────────────────────────────────────

def test_banner_includes_logo_and_tagline_and_version() -> None:
    out = render_startup_banner(
        theme=_PLAIN, version="0.1.0",
    )
    assert TAGLINE in out
    assert "v0.1.0" in out
    # one of the logo glyphs (full or compact) must appear
    assert ("█" in out) or ("_" in out)


def test_banner_mission_block_renders_id_status_objective() -> None:
    out = render_startup_banner(
        theme=_PLAIN, version="0.1.0",
        mode="mission",
        mission_id="mission_abc",
        mission_status="running",
        plan_mode="auto",
        max_rounds=20,
        objective="在 /tmp/foo 实现 X",
        state_dir="/home/u/.argus-skill/mission-state",
        daemon_pid=4242,
    )
    assert "mission_abc" in out
    assert "running" in out
    assert "auto" in out
    assert "max_rounds=20" in out
    assert "在 /tmp/foo 实现 X" in out
    assert "/home/u/.argus-skill/mission-state" in out
    assert "pid=4242" in out


def test_banner_truncates_long_objective() -> None:
    long = "x" * 200
    out = render_startup_banner(
        theme=_PLAIN, version="0.1.0", mode="mission",
        mission_id="m1", mission_status="running",
        plan_mode="auto", objective=long, state_dir="/tmp",
    )
    assert "…" in out
    # the objective line should not span more than ~110 chars excluding label
    obj_line = next(ln for ln in out.splitlines() if ln.startswith("  ") and "x" in ln)
    assert len(obj_line) < 130


def test_banner_queue_mode_renders_queue_label() -> None:
    out = render_startup_banner(
        theme=_PLAIN, version="0.1.0", mode="queue",
        state_dir="/tmp/state", daemon_pid=99,
    )
    assert "queue" in out.lower()
    assert "pid=99" in out


def test_banner_no_status_block_when_no_mode() -> None:
    out = render_startup_banner(theme=_PLAIN, version="0.1.0")
    assert TAGLINE in out
    assert "mission" not in out.lower() or "/help" in out  # no mission block


def test_banner_no_ansi_when_theme_disabled() -> None:
    out = render_startup_banner(
        theme=_PLAIN, version="0.1.0", mode="mission",
        mission_id="m", mission_status="done", plan_mode="auto",
    )
    assert "\x1b" not in out
