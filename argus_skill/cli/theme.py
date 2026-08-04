"""ANSI theme — colors, dim, bold, box-drawing constants.

Auto-detects whether ANSI is appropriate (TTY + ``NO_COLOR`` env var
respected) and downgrades to plain text otherwise. Tests construct
``Theme(enabled=True)`` explicitly so output stays deterministic.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass

# ── ANSI escape codes ──────────────────────────────────────────────────────

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"

# Foreground colors (8-color palette — broadest terminal compatibility).
_RED = "\x1b[31m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_BLUE = "\x1b[34m"
_MAGENTA = "\x1b[35m"
_CYAN = "\x1b[36m"
_GRAY = "\x1b[90m"  # bright black

# ── Truecolor palette (Catppuccin Mocha) ──────────────────────────────────
# The most widely-adopted 2024/25 dark terminal palette, tuned for exactly this
# use (careful surface→text contrast). When the terminal advertises 24-bit
# colour we emit these refined tones; otherwise we fall back to the 8-colour
# codes above so nothing breaks on a basic TTY. Names mirror the semantic role
# each Theme method plays, not the literal hue (e.g. ``magenta`` → mauve, the
# signature accent; ``cyan`` → sky; ``gray`` → overlay1).
_MOCHA: dict[str, tuple[int, int, int]] = {
    "red": (243, 139, 168),  # #f38ba8
    "green": (166, 227, 161),  # #a6e3a1
    "yellow": (249, 226, 175),  # #f9e2af
    "blue": (137, 180, 250),  # #89b4fa
    "magenta": (203, 166, 247),  # #cba6f7  mauve — signature accent
    "cyan": (137, 220, 235),  # #89dceb  sky
    "gray": (127, 132, 156),  # #7f849c  overlay1
}

_FALLBACK_SGR: dict[str, str] = {
    "red": _RED,
    "green": _GREEN,
    "yellow": _YELLOW,
    "blue": _BLUE,
    "magenta": _MAGENTA,
    "cyan": _CYAN,
    "gray": _GRAY,
}


def supports_truecolor() -> bool:
    """Best-effort 24-bit colour detection (multi-signal, like modern CLIs)."""
    if not sys.stdout.isatty():
        return False
    colorterm = os.environ.get("COLORTERM", "").lower()
    if colorterm in ("truecolor", "24bit"):
        return True
    term = os.environ.get("TERM", "")
    if "truecolor" in term or "direct" in term:
        return True
    if os.environ.get("VTE_VERSION"):
        return True
    if os.environ.get("TERM_PROGRAM", "") in (
        "iTerm.app",
        "WezTerm",
        "Hyper",
        "Tabby",
        "vscode",
        "ghostty",
    ):
        return True
    return False


# ── Box-drawing constants (always plain Unicode, no ANSI) ─────────────────

BOX = {"h": "─"}


# ── Theme ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Theme:
    """Minimal ANSI/box helper.

    Construct with ``Theme(enabled=False)`` for tests / non-TTY output;
    ``Theme.auto()`` checks the runtime environment.
    """

    enabled: bool = True
    width: int = 80
    truecolor: bool = False

    @classmethod
    def auto(cls, *, force: bool | None = None) -> "Theme":
        """Build a Theme honouring ``NO_COLOR`` env + TTY detection.

        ``force=True`` enables color even on non-TTY (useful when
        piping into ``less -R``); ``force=False`` disables it.
        """
        if force is True:
            enabled = True
        elif force is False:
            enabled = False
        else:
            if os.environ.get("NO_COLOR"):
                enabled = False
            else:
                enabled = sys.stdout.isatty()
        try:
            width = shutil.get_terminal_size((80, 24)).columns
        except OSError:
            width = 80
        # Cap to avoid super-wide lines on big monitors.
        width = max(40, min(width, 120))
        # Refined 24-bit palette only when the terminal advertises it; a basic
        # TTY transparently keeps the 8-colour codes.
        truecolor = enabled and supports_truecolor()
        return cls(enabled=enabled, width=width, truecolor=truecolor)

    # ── primitives ────────────────────────────────────────────────────

    def _wrap(self, text: str, *codes: str) -> str:
        if not self.enabled or not codes:
            return text
        return "".join(codes) + text + _RESET

    def _sgr(self, name: str) -> str:
        """Foreground SGR for a semantic colour name — 24-bit when the terminal
        supports it (Catppuccin Mocha), else the 8-colour fallback."""
        if self.truecolor and name in _MOCHA:
            r, g, b = _MOCHA[name]
            return f"\x1b[38;2;{r};{g};{b}m"
        return _FALLBACK_SGR.get(name, "")

    def bold(self, text: str) -> str:
        return self._wrap(text, _BOLD)

    def dim(self, text: str) -> str:
        return self._wrap(text, _DIM)

    def red(self, text: str) -> str:
        return self._wrap(text, self._sgr("red"))

    def green(self, text: str) -> str:
        return self._wrap(text, self._sgr("green"))

    def yellow(self, text: str) -> str:
        return self._wrap(text, self._sgr("yellow"))

    def magenta(self, text: str) -> str:
        return self._wrap(text, self._sgr("magenta"))

    def cyan(self, text: str) -> str:
        return self._wrap(text, self._sgr("cyan"))

    def gray(self, text: str) -> str:
        return self._wrap(text, self._sgr("gray"))

    def bold_green(self, text: str) -> str:
        return self._wrap(text, _BOLD, self._sgr("green"))

    def bold_red(self, text: str) -> str:
        return self._wrap(text, _BOLD, self._sgr("red"))

    def bold_cyan(self, text: str) -> str:
        return self._wrap(text, _BOLD, self._sgr("cyan"))

    def bold_blue(self, text: str) -> str:
        return self._wrap(text, _BOLD, self._sgr("blue"))

    def bold_magenta(self, text: str) -> str:
        return self._wrap(text, _BOLD, self._sgr("magenta"))

    def bold_yellow(self, text: str) -> str:
        return self._wrap(text, _BOLD, self._sgr("yellow"))

    # ── box drawing ───────────────────────────────────────────────────

    def hr(self, label: str | None = None) -> str:
        """Horizontal rule, optionally with a centered label.

        Returns one line ≤ ``self.width`` characters.
        """
        w = self.width
        if not label:
            return self.dim(BOX["h"] * w)
        # ── label ── pattern. Be generous with spacing.
        pad = f"  {label}  "
        side = max(3, (w - len(pad)) // 2)
        line = BOX["h"] * side + pad + BOX["h"] * (w - side - len(pad))
        return self.dim(line[:w])


def default_theme() -> Theme:
    """Module-level convenience — auto-detect TTY + NO_COLOR."""
    return Theme.auto()
