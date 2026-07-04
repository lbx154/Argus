"""ANSI theme — colors, dim, bold, box-drawing constants.

Auto-detects whether ANSI is appropriate (TTY + ``NO_COLOR`` env var
respected) and downgrades to plain text otherwise. Tests construct
``Theme(enabled=True)`` explicitly so output stays deterministic.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
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

# ── Truecolor palette (Catppuccin Mocha) ──────────────────────────────────
# The most widely-adopted 2024/25 dark terminal palette, tuned for exactly this
# use (careful surface→text contrast). When the terminal advertises 24-bit
# colour we emit these refined tones; otherwise we fall back to the 8-colour
# codes above so nothing breaks on a basic TTY. Names mirror the semantic role
# each Theme method plays, not the literal hue (e.g. ``magenta`` → mauve, the
# signature accent; ``cyan`` → sky; ``gray`` → overlay1).
_MOCHA: dict[str, tuple[int, int, int]] = {
    "red": (243, 139, 168),      # #f38ba8
    "green": (166, 227, 161),    # #a6e3a1
    "yellow": (249, 226, 175),   # #f9e2af
    "blue": (137, 180, 250),     # #89b4fa
    "magenta": (203, 166, 247),  # #cba6f7  mauve — signature accent
    "cyan": (137, 220, 235),     # #89dceb  sky
    "gray": (127, 132, 156),     # #7f849c  overlay1
}

_FALLBACK_SGR: dict[str, str] = {
    "red": _RED, "green": _GREEN, "yellow": _YELLOW, "blue": _BLUE,
    "magenta": _MAGENTA, "cyan": _CYAN, "gray": _GRAY,
}

# Default banner gradient: mauve → blue → teal — cool, modern, on-brand for a
# coding agent (warm gradients read as "playful", cool reads as "precise").
_LOGO_GRADIENT = ["#cba6f7", "#89b4fa", "#94e2d5"]

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * t)) for i in range(3))  # type: ignore[return-value]


def _gradient_rgb_at(stops_rgb: list[tuple[int, int, int]], t: float) -> tuple[int, int, int]:
    """Colour at position ``t`` in [0,1] across a multi-stop gradient."""
    if len(stops_rgb) == 1:
        return stops_rgb[0]
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    seg = t * (len(stops_rgb) - 1)
    idx = min(int(seg), len(stops_rgb) - 2)
    return _lerp(stops_rgb[idx], stops_rgb[idx + 1], seg - idx)


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
        "iTerm.app", "WezTerm", "Hyper", "Tabby", "vscode", "ghostty",
    ):
        return True
    return False


def visible_len(text: str) -> int:
    """Printable column width of ``text`` with ANSI color codes stripped.

    Lets callers decide whether an already-colored, multi-span line (built
    from several ``theme.xxx()`` calls concatenated together) fits the
    terminal width before printing it.
    """
    return len(_ANSI_RE.sub("", text))


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

    # ── wrapping ──────────────────────────────────────────────────────

    def wrap_after(
        self,
        text: str,
        *,
        first_indent: int,
        hang_indent: int = 2,
        width: int | None = None,
    ) -> list[str]:
        """Word-wrap ``text`` to continue after a prefix already on screen.

        ``first_indent`` is how many printable columns the caller already
        wrote on the current line (e.g. ``"  warn       → "``); the first
        returned line omits that many columns (the caller already printed
        them) while later lines are padded with ``hang_indent`` spaces so
        continuations align under the message body instead of colliding
        with the margin or splitting a word/command across the wrap.

        Colors must be applied to each returned line separately (join with
        ``"\\n"`` after coloring) — this only wraps plain text, so a single
        ANSI span never gets cut mid-escape-sequence.
        """
        w = width if width is not None else self.width
        body = " ".join(str(text).split())
        wrapped = textwrap.wrap(
            body,
            width=max(20, w),
            initial_indent=" " * first_indent,
            subsequent_indent=" " * hang_indent,
            break_long_words=False,
            break_on_hyphens=False,
        )
        if not wrapped:
            return [""]
        wrapped[0] = wrapped[0][first_indent:]
        return wrapped

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

    def italic(self, text: str) -> str:
        return self._wrap(text, _ITALIC)

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

    # ── gradient ──────────────────────────────────────────────────────

    def gradient(
        self,
        text: str,
        stops: list[str] | None = None,
        *,
        bold: bool = True,
        width: int | None = None,
        offset: int = 0,
    ) -> str:
        """Per-character horizontal colour gradient across ``text``.

        On a 24-bit terminal each character gets its own interpolated tone
        (mauve→blue→teal by default); on a basic colour TTY it degrades to a
        single bold accent (the signature mauve); with colour off it is the
        plain text. ``width``/``offset`` let a multi-row banner share one
        colour ramp across the whole block so every row's columns line up in
        hue (pass the block's max width as ``width``).
        """
        if not self.enabled:
            return text
        if not self.truecolor:
            codes = (_BOLD, self._sgr("magenta")) if bold else (self._sgr("magenta"),)
            return self._wrap(text, *codes)
        rgb_stops = [_hex_to_rgb(s) for s in (stops or _LOGO_GRADIENT)]
        span = max(1, (width if width is not None else len(text)) - 1)
        pre = _BOLD if bold else ""
        out: list[str] = []
        for i, ch in enumerate(text):
            if ch == " ":
                out.append(ch)
                continue
            r, g, b = _gradient_rgb_at(rgb_stops, (offset + i) / span)
            out.append(f"{pre}\x1b[38;2;{r};{g};{b}m{ch}")
        return "".join(out) + _RESET

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
        return "\n".join(
            [self.dim(top)] + [self.dim(BOX["v"]) + " " + ln + " " * (body_w - len(ln) + 1) + self.dim(BOX["v"])
                              for ln in clipped] + [self.dim(bot)]
        )


def default_theme() -> Theme:
    """Module-level convenience — auto-detect TTY + NO_COLOR."""
    return Theme.auto()
