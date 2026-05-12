"""Tests for the lightweight ANSI/box theme."""

from __future__ import annotations

from unittest import mock

from argus_skill.cli.theme import BOX, Theme

# ── color toggling ────────────────────────────────────────────────────────

def test_theme_disabled_passes_text_through() -> None:
    t = Theme(enabled=False)
    assert t.bold("hi") == "hi"
    assert t.red("oops") == "oops"
    assert t.bold_green("ok") == "ok"


def test_theme_enabled_wraps_with_ansi() -> None:
    t = Theme(enabled=True)
    assert t.bold("hi") == "\x1b[1mhi\x1b[0m"
    out = t.bold_green("ok")
    assert out.startswith("\x1b[")
    assert out.endswith("\x1b[0m")
    assert "ok" in out


def test_theme_auto_disabled_when_no_color_env(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    t = Theme.auto()
    assert t.enabled is False


def test_theme_auto_force_true_overrides_no_color(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    t = Theme.auto(force=True)
    assert t.enabled is True


def test_theme_auto_force_false() -> None:
    t = Theme.auto(force=False)
    assert t.enabled is False


def test_theme_auto_disabled_when_not_a_tty(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    with mock.patch("sys.stdout.isatty", return_value=False):
        t = Theme.auto()
    assert t.enabled is False


# ── horizontal rule ───────────────────────────────────────────────────────

def test_hr_no_label_is_only_dashes() -> None:
    t = Theme(enabled=False, width=20)
    assert t.hr() == BOX["h"] * 20


def test_hr_with_label_is_centered() -> None:
    t = Theme(enabled=False, width=30)
    out = t.hr("Round 5")
    assert "Round 5" in out
    assert out.startswith(BOX["h"])
    assert out.endswith(BOX["h"])
    assert len(out) == 30


# ── boxed (full rectangle) ────────────────────────────────────────────────

def test_boxed_creates_top_and_bottom_borders() -> None:
    t = Theme(enabled=False, width=30)
    out = t.boxed(["foo", "bar"])
    lines = out.splitlines()
    assert lines[0].startswith(BOX["tl"])
    assert lines[0].endswith(BOX["tr"])
    assert lines[-1].startswith(BOX["bl"])
    assert lines[-1].endswith(BOX["br"])
    # All body lines start AND end with the vertical bar.
    for body in lines[1:-1]:
        assert body.startswith(BOX["v"])
        assert body.endswith(BOX["v"])


def test_boxed_with_title_centers_in_top_border() -> None:
    t = Theme(enabled=False, width=40)
    out = t.boxed(["x"], title="hello")
    top = out.splitlines()[0]
    assert "hello" in top
    assert top.startswith(BOX["tl"]) and top.endswith(BOX["tr"])


# ── left-bordered box (CJK-safe) ──────────────────────────────────────────

def test_left_box_only_has_left_border() -> None:
    t = Theme(enabled=False, width=80)
    out = t.left_box(["line one", "line two"], title="hdr")
    lines = out.splitlines()
    assert lines[0].startswith(BOX["left_top"])
    assert "hdr" in lines[0]
    # Body lines start with vertical bar, no right border.
    for body in lines[1:-1]:
        assert body.startswith(BOX["v"])
        # No vertical bar at end (CJK-safe).
        assert not body.rstrip().endswith(BOX["v"])
    assert lines[-1].startswith(BOX["left_bot"])


def test_left_box_handles_cjk_content() -> None:
    """CJK characters should not break the left-box layout."""
    t = Theme(enabled=False, width=80)
    out = t.left_box(["在 /tmp/argus 创建 word_freq.py"], title="status")
    # Just ensure it doesn't crash and the CJK survives.
    assert "word_freq.py" in out
    assert "创建" in out
