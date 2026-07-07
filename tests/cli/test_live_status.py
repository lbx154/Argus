"""Tests for the inline animated status line (cli/live_status.py).

Deterministic: every test injects an explicit ``stream``, ``clock`` and
``enabled`` so nothing depends on real TTY detection, wall-clock timing, or the
background thread's scheduling.
"""
from __future__ import annotations

import io
import time

import pytest

from argus_skill.cli.live_status import (
    FRAMES,
    LiveStatus,
    _fmt_elapsed,
    _spinner_enabled,
)
from argus_skill.cli.theme import Theme


# ── enable / disable gating ──────────────────────────────────────────────

def test_disabled_when_stream_not_a_tty():
    ls = LiveStatus("x", stream=io.StringIO())
    assert ls.enabled is False


def test_disabled_respects_no_color(monkeypatch):
    class _TTY(io.StringIO):
        def isatty(self):  # noqa: D401
            return True

    monkeypatch.setenv("NO_COLOR", "1")
    assert _spinner_enabled(_TTY()) is False


def test_disabled_respects_opt_out_env(monkeypatch):
    class _TTY(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_NO_SPINNER", "1")
    assert _spinner_enabled(_TTY()) is False


def test_enabled_on_real_tty(monkeypatch):
    class _TTY(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_NO_SPINNER", raising=False)
    assert _spinner_enabled(_TTY()) is True


def test_disabled_context_writes_nothing():
    buf = io.StringIO()
    with LiveStatus("thinking…", stream=buf, enabled=False) as ls:
        assert ls.enabled is False
    assert buf.getvalue() == ""  # byte-for-byte no-op when disabled


# ── elapsed formatting ───────────────────────────────────────────────────

@pytest.mark.parametrize("secs,expected", [
    (0, "0s"), (3, "3s"), (59, "59s"), (60, "1m 0s"), (75, "1m 15s"), (605, "10m 5s"),
])
def test_fmt_elapsed(secs, expected):
    assert _fmt_elapsed(secs) == expected


# ── frame rendering ──────────────────────────────────────────────────────

def test_render_frame_has_spinner_label_elapsed_and_erase():
    ls = LiveStatus("思考中…", stream=io.StringIO(), enabled=True, clock=lambda: 3.0)
    ls._start = 0.0
    frame = ls.render_frame()
    assert frame.startswith("\r\x1b[2K")     # erases the line first
    assert FRAMES[0] in frame                # current spinner glyph
    assert "思考中…" in frame                 # the label
    assert "3s" in frame                     # elapsed
    assert "Ctrl-C to cancel" in frame       # hint


def test_render_frame_advances_spinner_glyph():
    ls = LiveStatus("x", stream=io.StringIO(), enabled=True, clock=lambda: 0.0)
    ls._start = 0.0
    ls._frame = 0
    assert FRAMES[0] in ls.render_frame()
    ls._frame = 3
    assert FRAMES[3] in ls.render_frame()


def test_render_frame_theme_colors_applied():
    ls = LiveStatus("x", theme=Theme(enabled=True), stream=io.StringIO(),
                    enabled=True, clock=lambda: 1.0)
    ls._start = 0.0
    assert "\x1b[" in ls.render_frame()  # ANSI colour codes present


def test_render_frame_plain_without_theme():
    ls = LiveStatus("x", theme=None, stream=io.StringIO(),
                    enabled=True, clock=lambda: 1.0)
    ls._start = 0.0
    frame = ls.render_frame()
    # Only the erase-line control sequence — no colour SGR codes.
    assert frame.startswith("\r\x1b[2K")
    assert "\x1b[33m" not in frame and "\x1b[1m" not in frame


def _visible_width(frame: str) -> int:
    """Display width of a rendered frame, minus control sequences."""
    import re
    import unicodedata

    body = frame
    for ctrl in ("\r\x1b[2K", "\r\x1b[2K"):
        if body.startswith(ctrl):
            body = body[len(ctrl):]
            break
    body = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", body)  # strip remaining SGR/CSI
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in body)


def test_render_frame_clamps_long_label_to_terminal_width(monkeypatch):
    """A long label on a narrow terminal must never exceed width-1 columns, else
    the wrapped overflow row survives the next \\r-erase and the status line
    cascades into a flood of duplicated lines (regression)."""
    import shutil

    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda fallback=(80, 24): __import__(
            "os").terminal_size((40, 24))
    )
    long_label = "SELF: one Codex handling " + "how do you know what type of model you are " * 4
    ls = LiveStatus(long_label, stream=io.StringIO(), enabled=True,
                    clock=lambda: 6.0)
    ls._start = 0.0
    frame = ls.render_frame()
    assert _visible_width(frame) <= 40 - 1     # fits within width-1, no wrap
    assert frame.startswith("\r\x1b[2K")
    assert FRAMES[0] in frame                   # spinner still present
    assert "…" in frame                         # was truncated with an ellipsis


def test_render_frame_wide_terminal_keeps_full_label_and_meta(monkeypatch):
    import shutil

    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda fallback=(80, 24): __import__(
            "os").terminal_size((200, 24))
    )
    ls = LiveStatus("short label", stream=io.StringIO(), enabled=True,
                    clock=lambda: 3.0)
    ls._start = 0.0
    frame = ls.render_frame()
    assert "short label" in frame
    assert "3s" in frame          # meta/timer preserved on a wide terminal
    assert "…" not in frame       # nothing clipped


# ── phrase rotation + live update ────────────────────────────────────────

def test_phrases_rotate_over_time():
    clock = {"t": 0.0}
    ls = LiveStatus(
        "a", stream=io.StringIO(), enabled=True,
        phrases=["one", "two", "three"], phrase_interval=5.0,
        clock=lambda: clock["t"],
    )
    ls._start = 0.0
    clock["t"] = 0.0
    assert ls._current_label() == "one"
    clock["t"] = 5.0
    assert ls._current_label() == "two"
    clock["t"] = 12.0  # 12 / 5 = 2 → third phrase
    assert ls._current_label() == "three"
    clock["t"] = 16.0  # 16 / 5 = 3 → index 3 % 3 = 0 → wraps to first
    assert ls._current_label() == "one"


def test_update_changes_label_when_no_phrases():
    ls = LiveStatus("first", stream=io.StringIO(), enabled=True, clock=lambda: 0.0)
    ls._start = 0.0
    assert ls._current_label() == "first"
    ls.update("second")
    assert ls._current_label() == "second"


def test_explicit_update_wins_over_phrase_rotation():
    """A real ``update()`` (e.g. an on_phase callback firing) must permanently
    override cosmetic ``phrases`` rotation — regression test for a bug where
    ``_current_label`` ignored ``update()``/``update_role()`` entirely
    whenever ``phrases`` was non-empty, so a caller like the REPL's
    manager-triage spinner (which passes both a cosmetic fallback AND drives
    real progress via on_phase) never showed real phase text."""
    clock = {"t": 0.0}
    ls = LiveStatus(
        "a", stream=io.StringIO(), enabled=True,
        phrases=["one", "two"], phrase_interval=5.0,
        clock=lambda: clock["t"],
    )
    ls._start = 0.0
    assert ls._current_label() == "one"  # still rotating before any real event
    clock["t"] = 5.0
    assert ls._current_label() == "two"
    ls.update("Engineer · writing code")
    assert ls._current_label() == "Engineer · writing code"
    # Time keeps moving — the explicit label must stick, not resume rotating.
    clock["t"] = 12.0
    assert ls._current_label() == "Engineer · writing code"
    ls.update_role("bold_green", "Reviewer · 裁决中")
    assert ls._current_label() == "Reviewer · 裁决中"


def test_update_accent_retints_spinner_glyph_only():
    ls = LiveStatus(
        "x", theme=Theme(enabled=True), stream=io.StringIO(),
        enabled=True, clock=lambda: 0.0,
    )
    ls._start = 0.0
    before = ls.render_frame()
    assert Theme(enabled=True).magenta(FRAMES[0]) in before
    ls.update_accent("bold_green")
    after = ls.render_frame()
    assert Theme(enabled=True).bold_green(FRAMES[0]) in after
    # The label text itself is unaffected by the accent change.
    assert "x" in after


def test_update_role_sets_accent_and_label_together():
    ls = LiveStatus("idle", stream=io.StringIO(), enabled=True, clock=lambda: 0.0)
    ls._start = 0.0
    ls.update_role("bold_yellow", "Reviewer · 裁决中")
    assert ls._accent == "bold_yellow"
    assert ls._current_label() == "Reviewer · 裁决中"


def test_update_accent_ignores_blank_value():
    ls = LiveStatus("x", stream=io.StringIO(), enabled=True, clock=lambda: 0.0)
    ls.update_accent("   ")
    assert ls._accent == "magenta"  # unchanged default


def test_empty_label_falls_back():
    ls = LiveStatus("", stream=io.StringIO(), enabled=True, clock=lambda: 0.0)
    assert ls._current_label() == "working…"


# ── context manager lifecycle (real thread, real stream) ─────────────────

def test_enabled_context_animates_then_erases():
    buf = io.StringIO()
    with LiveStatus("thinking…", stream=buf, enabled=True, interval=0.02):
        time.sleep(0.1)  # let the daemon thread paint a few frames
    out = buf.getvalue()
    assert "\x1b[?25l" in out          # cursor hidden on enter
    assert "thinking…" in out          # animated at least once
    assert out.endswith("\r\x1b[2K\x1b[?25h")  # erased + cursor restored on exit


def test_exit_does_not_suppress_exceptions():
    buf = io.StringIO()
    with pytest.raises(ValueError):
        with LiveStatus("x", stream=buf, enabled=True, interval=0.02):
            raise ValueError("boom")
    # even on exception the line is cleaned up
    assert buf.getvalue().endswith("\r\x1b[2K\x1b[?25h")


def test_broken_stream_does_not_crash():
    class _Broken(io.StringIO):
        def write(self, *_a):  # noqa: D401
            raise OSError("pipe closed")

    # Must not raise even though every write fails.
    with LiveStatus("x", stream=_Broken(), enabled=True, interval=0.02):
        time.sleep(0.05)
