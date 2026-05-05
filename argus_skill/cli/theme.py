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
_ITALIC = "\x1b[3m"

# Foreground colors (8-color palette — broadest terminal compatibility).
_RED = "\x1b[31m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_BLUE = "\x1b[34m"
_MAGENTA = "\x1b[35m"
_CYAN = "\x1b[36m"
_GRAY = "\x1b[90m"  # bright black


# ── Box-drawing constants (always plain Unicode, no ANSI) ─────────────────

BOX = {
    "tl": "╭",
    "tr": "╮",
    "bl": "╰",
    "br": "╯",
    "h": "─",
    "v": "│",
    "vl": "├",
    "vr": "┤",
    "left_top": "┌",
    "left_bot": "└",
    "left_mid": "├",
}


# ── Theme ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Theme:
    """Minimal ANSI/box helper.

    Construct with ``Theme(enabled=False)`` for tests / non-TTY output;
    ``Theme.auto()`` checks the runtime environment.
    """

    enabled: bool = True
    width: int = 80

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
        return cls(enabled=enabled, width=width)

    # ── primitives ────────────────────────────────────────────────────

    def _wrap(self, text: str, *codes: str) -> str:
        if not self.enabled or not codes:
            return text
        return "".join(codes) + text + _RESET

    def bold(self, text: str) -> str:
        return self._wrap(text, _BOLD)

    def dim(self, text: str) -> str:
        return self._wrap(text, _DIM)

    def italic(self, text: str) -> str:
        return self._wrap(text, _ITALIC)

    def red(self, text: str) -> str:
        return self._wrap(text, _RED)

    def green(self, text: str) -> str:
        return self._wrap(text, _GREEN)

    def yellow(self, text: str) -> str:
        return self._wrap(text, _YELLOW)

    def blue(self, text: str) -> str:
        return self._wrap(text, _BLUE)

    def magenta(self, text: str) -> str:
        return self._wrap(text, _MAGENTA)

    def cyan(self, text: str) -> str:
        return self._wrap(text, _CYAN)

    def gray(self, text: str) -> str:
        return self._wrap(text, _GRAY)

    def bold_green(self, text: str) -> str:
        return self._wrap(text, _BOLD, _GREEN)

    def bold_red(self, text: str) -> str:
        return self._wrap(text, _BOLD, _RED)

    def bold_cyan(self, text: str) -> str:
        return self._wrap(text, _BOLD, _CYAN)

    def bold_blue(self, text: str) -> str:
        return self._wrap(text, _BOLD, _BLUE)

    def bold_magenta(self, text: str) -> str:
        return self._wrap(text, _BOLD, _MAGENTA)

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

    def left_box(self, lines: list[str], *, title: str | None = None) -> str:
        """Render ``lines`` with a left-side box decoration only.

        Skips a right border so CJK / emoji width quirks don't break
        alignment. The first line gets ``┌─`` if ``title`` is given,
        else nothing special; the last line gets ``└─``.

        ::

            ┌─ status header
            │  body line 1
            │  body line 2
            └─
        """
        out: list[str] = []
        bar = self.dim(BOX["v"]) + "  "
        if title is not None:
            out.append(self.dim(BOX["left_top"] + BOX["h"] + " ") + title)
        for ln in lines:
            out.append(bar + ln)
        out.append(self.dim(BOX["left_bot"] + BOX["h"]))
        return "\n".join(out)

    def boxed(
        self, lines: list[str], *, title: str | None = None
    ) -> str:
        """Full four-sided box. Use only for ASCII content (no CJK).

        Pads each line with spaces to a fixed width; this means CJK
        content WILL break alignment. For mixed content use
        ``left_box`` instead.
        """
        # Compute box content width: theme.width - 4 (left/right borders + 2 pad).
        body_w = max(20, min(self.width, 120) - 4)
        clipped = [
            (ln if len(ln) <= body_w else ln[: body_w - 1] + "…")
            for ln in lines
        ]
        h = BOX["h"]
        # Top
        if title is None:
            top = BOX["tl"] + h * (body_w + 2) + BOX["tr"]
        else:
            label = f" {title} "
            side = max(2, (body_w + 2 - len(label)) // 2)
            top_line = (
                BOX["tl"]
                + h * side
                + label
                + h * (body_w + 2 - side - len(label))
                + BOX["tr"]
            )
            top = top_line
        bot = BOX["bl"] + h * (body_w + 2) + BOX["br"]
        body = [
            BOX["v"] + " " + ln + " " * (body_w - len(ln) + 1) + BOX["v"]
            for ln in clipped
        ]
        return "\n".join(
            [self.dim(top)] + [self.dim(BOX["v"]) + " " + ln + " " * (body_w - len(ln) + 1) + self.dim(BOX["v"])
                              for ln in clipped] + [self.dim(bot)]
        )


def default_theme() -> Theme:
    """Module-level convenience — auto-detect TTY + NO_COLOR."""
    return Theme.auto()
