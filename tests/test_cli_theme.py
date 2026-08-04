"""Tests for the lightweight ANSI and left-border terminal theme."""

from __future__ import annotations

from unittest import mock

from argus_skill.cli.theme import BOX, Theme


def test_theme_disabled_passes_text_through() -> None:
    theme = Theme(enabled=False)
    assert theme.bold("hi") == "hi"
    assert theme.red("oops") == "oops"
    assert theme.bold_green("ok") == "ok"


def test_theme_enabled_wraps_with_ansi() -> None:
    theme = Theme(enabled=True)
    assert theme.bold("hi") == "\x1b[1mhi\x1b[0m"
    out = theme.bold_green("ok")
    assert out.startswith("\x1b[")
    assert out.endswith("\x1b[0m")
    assert "ok" in out


def test_bold_yellow_wraps_bold_plus_yellow() -> None:
    theme = Theme(enabled=True, truecolor=True)
    out = theme.bold_yellow("x")
    assert out.startswith("\x1b[1m\x1b[38;2;249;226;175m")
    assert out.endswith("\x1b[0m")
    assert Theme(enabled=False).bold_yellow("x") == "x"


def test_theme_auto_disabled_when_no_color_env(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert Theme.auto().enabled is False


def test_theme_auto_force_true_overrides_no_color(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert Theme.auto(force=True).enabled is True


def test_theme_auto_force_false() -> None:
    assert Theme.auto(force=False).enabled is False


def test_theme_auto_disabled_when_not_a_tty(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    with mock.patch("sys.stdout.isatty", return_value=False):
        assert Theme.auto().enabled is False


def test_hr_no_label_is_only_dashes() -> None:
    theme = Theme(enabled=False, width=20)
    assert theme.hr() == BOX["h"] * 20


def test_hr_with_label_is_centered() -> None:
    theme = Theme(enabled=False, width=30)
    out = theme.hr("Round 5")
    assert "Round 5" in out
    assert out.startswith(BOX["h"])
    assert out.endswith(BOX["h"])
    assert len(out) == 30


def test_truecolor_off_by_default_keeps_8color() -> None:
    theme = Theme(enabled=True)
    assert theme.truecolor is False
    assert theme.red("x") == "\x1b[31mx\x1b[0m"
    assert theme.cyan("x") == "\x1b[36mx\x1b[0m"


def test_truecolor_emits_24bit_sgr() -> None:
    theme = Theme(enabled=True, truecolor=True)
    assert theme.red("x") == "\x1b[38;2;243;139;168mx\x1b[0m"
    assert theme.magenta("x") == "\x1b[38;2;203;166;247mx\x1b[0m"
    out = theme.bold_blue("x")
    assert out.startswith("\x1b[1m\x1b[38;2;137;180;250m")
    assert out.endswith("\x1b[0m")


def test_supports_truecolor_reads_colorterm(monkeypatch) -> None:
    from argus_skill.cli import theme as theme_mod

    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert theme_mod.supports_truecolor() is True
    monkeypatch.setenv("COLORTERM", "")
    monkeypatch.delenv("VTE_VERSION", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("TERM_PROGRAM", "")
    assert theme_mod.supports_truecolor() is False
