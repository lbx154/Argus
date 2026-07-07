"""Inline, self-erasing animated status line for synchronous work.

The line REPL blocks while the Manager triages a message or drafts a plan.
Before this, the operator saw a frozen prompt with no feedback — the "卡住"
feeling. :class:`LiveStatus` renders a Claude-Code / Codex / Gemini-style
single-line animated indicator during that wait::

    ⠸ 思考中…  (3s · Ctrl-C to cancel)

Design (matches the CLIs we benchmarked):

* **Braille spinner** ``⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`` advanced every ~80 ms — the same frames
  and cadence as OpenAI Codex CLI (``status_surfaces.rs``) and Rich's ``dots``.
* **Elapsed timer** — ``{N}s`` under a minute, ``{M}m {N}s`` beyond, like
  Gemini CLI's ``(esc to cancel, {N}s)``.
* **Rotating phrases** (optional) — a small honest set the label cycles through
  while working, so a long wait still feels alive (Gemini's phrase cycler).
* **In place** — every frame rewrites the current line (``\r`` + erase-to-EOL),
  so it never spams scrollback and fully erases itself on exit, leaving the
  caller's real output to start on a clean line. Copy/paste and scrollback are
  preserved (unlike a full-screen takeover).

It is a no-op unless stdout is a real TTY, colour is allowed, and the operator
has not opted out (``NO_COLOR`` / ``ARGUS_SKILL_NO_SPINNER``) — so piped output,
captured test stdout, and the event-sink paths stay byte-for-byte unchanged.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from typing import Callable, Sequence, TextIO

# ANSI + CJK-aware clip helpers live in ``cli.roles_status`` (well-tested). They
# are pure text utilities and ``roles_status`` never imports this module, so a
# top-level import is cycle-free. Fall back to identity clips if anything about
# that module changes, so the spinner never crashes the work it decorates.
try:  # pragma: no cover — exercised indirectly via render_frame tests
    from .roles_status import _clip_ansi_line, _clip_display, _disp_width
except Exception:  # noqa: BLE001 — never let a helper import break the spinner

    def _disp_width(text: str) -> int:  # type: ignore[misc]
        return len(text)

    def _clip_display(text: str, budget: int) -> str:  # type: ignore[misc]
        return text if len(text) <= budget else text[: max(0, budget - 1)] + "…"

    def _clip_ansi_line(s: str, budget: int) -> str:  # type: ignore[misc]
        return s

# Braille dot-spinner — identical frames/cadence to Codex CLI + Rich `dots`.
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_INTERVAL = 0.08  # 80 ms per frame (~12 fps) — smooth braille, low CPU.

# ANSI: erase whole line, hide/show cursor. Colour handled via the Theme.
_ERASE_LINE = "\r\x1b[2K"
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"

_TRUTHY = {"1", "true", "yes", "on"}


def _spinner_enabled(stream: TextIO) -> bool:
    """True when an animated status line is appropriate for ``stream``."""
    if os.environ.get("ARGUS_SKILL_NO_SPINNER", "").strip().lower() in _TRUTHY:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001 — odd stream objects → treat as non-TTY
        return False


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60}s"


class LiveStatus:
    """Animated single-line status shown while a blocking call runs.

    Use as a context manager::

        with LiveStatus("思考中…", theme=theme):
            reply = manager_triage(...)

    The spinner animates on a daemon thread and erases itself on ``__exit__``
    (including on exception / ``KeyboardInterrupt``), so the caller's next
    ``print`` starts on a clean line.

    Parameters are injectable for tests: pass an explicit ``stream``,
    ``enabled``, and ``clock`` and call :meth:`render_frame` directly without
    ever starting the thread.
    """

    def __init__(
        self,
        label: str = "thinking…",
        *,
        theme: object | None = None,
        stream: TextIO | None = None,
        interval: float = _INTERVAL,
        phrases: Sequence[str] | None = None,
        phrase_interval: float = 6.0,
        hint: str = "Ctrl-C to cancel",
        enabled: bool | None = None,
        clock: Callable[[], float] | None = None,
        accent: str = "magenta",
    ) -> None:
        self._stream: TextIO = stream if stream is not None else sys.stdout
        self._theme = theme
        self._interval = max(0.02, float(interval))
        self._label = str(label or "").strip() or "working…"
        self._phrases = [str(p).strip() for p in (phrases or []) if str(p).strip()]
        self._phrase_interval = max(1.0, float(phrase_interval))
        self._hint = str(hint or "").strip()
        self._clock = clock or time.monotonic
        self._enabled = (
            _spinner_enabled(self._stream) if enabled is None else bool(enabled)
        )
        self._frame = 0
        self._start = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active = False
        # Set the moment ``update``/``update_role`` is first called — from then
        # on ``_current_label`` shows the explicit label instead of rotating
        # ``phrases`` (see ``_current_label``'s docstring: "an explicit update
        # wins"). Without this, a caller that passes BOTH ``phrases`` (a cosmetic
        # fallback for the pre-first-event silence) AND drives real progress via
        # ``update()`` — e.g. the REPL's manager-triage spinner — would have its
        # real phase text silently discarded forever in favour of the rotating
        # placeholder, which is exactly backwards.
        self._explicit_update = False
        # The glyph's colour method (a mauve "magenta" by default — the brand
        # accent). ``update_accent`` lets a multi-role caller retint the
        # spinner to whichever role is acting right now (e.g. its
        # ``cli.roles_status.ROLE_COLOR_BOLD`` method name) — a moving
        # colour "baton" — without touching the label text at all, so there
        # is no risk of nested ANSI resets truncating the label's own styling.
        self._accent = str(accent or "magenta").strip() or "magenta"

    # ── public API ───────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    def update(self, label: str) -> None:
        """Thread-safe: change the phase label mid-flight.

        From this call on, ``_current_label`` shows this (and later) explicit
        label instead of rotating ``phrases`` — an explicit update always wins.
        """
        with self._lock:
            self._label = str(label or "").strip() or self._label
            self._explicit_update = True

    def update_accent(self, accent: str) -> None:
        """Thread-safe: retint the spinner glyph (e.g. to the role now driving
        progress), independent of the label text."""
        accent = str(accent or "").strip()
        if not accent:
            return
        with self._lock:
            self._accent = accent

    def update_role(self, accent: str, label: str) -> None:
        """Convenience: set the label and the glyph accent in one call — the
        common case when a new event names both "who" and "what"."""
        self.update_accent(accent)
        self.update(label)


    def __enter__(self) -> "LiveStatus":
        if not self._enabled:
            return self
        self._start = self._clock()
        self._active = True
        try:
            self._stream.write(_HIDE_CURSOR)
            self._stream.flush()
        except Exception:  # noqa: BLE001
            pass
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> bool:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=1.0)
        if self._active:
            try:
                self._stream.write(_ERASE_LINE + _SHOW_CURSOR)
                self._stream.flush()
            except Exception:  # noqa: BLE001
                pass
            self._active = False
        return False  # never suppress exceptions (Ctrl-C must propagate)

    # ── internals (also the test surface) ────────────────────────────────

    def _current_label(self) -> str:
        """Label for this instant: an explicit update wins; else rotate phrases."""
        with self._lock:
            label = self._label
            explicit = self._explicit_update
        if self._phrases and not explicit:
            idx = int((self._clock() - self._start) / self._phrase_interval)
            return self._phrases[idx % len(self._phrases)]
        return label

    def _color(self, method: str, text: str) -> str:
        """Apply ``theme.<method>`` if the theme offers it; plain otherwise."""
        theme = self._theme
        if theme is None or not text:
            return text
        fn = getattr(theme, method, None)
        if not callable(fn):
            return text
        try:
            return str(fn(text))
        except Exception:  # noqa: BLE001
            return text

    def render_frame(self) -> str:
        """The full escape sequence for one repaint (no thread needed).

        The composed line is clamped to ``terminal width - 1`` display columns
        so it can never wrap: a wrapped line would leave the overflow row behind
        when the next frame's ``\\r\\x1b[2K`` only erases the final physical row,
        producing a cascade of duplicated status lines. The elapsed/hint meta is
        preserved by budgeting the (plain) label first, with a whole-line clip as
        the final safety net.
        """
        spin = FRAMES[self._frame % len(FRAMES)]
        label = self._current_label()
        with self._lock:
            accent = self._accent
        elapsed = _fmt_elapsed(self._clock() - self._start)
        meta = f"({elapsed}" + (f" · {self._hint}" if self._hint else "") + ")"

        # Budget the plain label so spinner (1) + spaces (1 + 2) + meta survive.
        try:
            cols = shutil.get_terminal_size((80, 24)).columns
        except Exception:  # noqa: BLE001
            cols = 80
        budget = max(1, cols - 1)
        fixed = _disp_width(spin) + 1 + 2 + _disp_width(meta)
        label_budget = budget - fixed
        if label_budget >= 8:
            label = _clip_display(label, label_budget)
        else:
            # Terminal too narrow to keep both label and meta — drop meta and
            # give the whole budget to the label instead.
            meta = ""
            label = _clip_display(label, max(1, budget - _disp_width(spin) - 1))

        body = (
            self._color(accent, spin)  # mauve by default; a multi-role caller
            # may retint this per active role via ``update_accent``/``update_role``
            + " "
            + self._color("bold", label)
            + ("  " + self._color("dim", meta) if meta else "")
        )
        # Final safety net: clamp the fully-composed (ANSI-colored) line so no
        # residual styling width pushes it past the terminal edge.
        body = _clip_ansi_line(body, budget)
        return _ERASE_LINE + body

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._stream.write(self.render_frame())
                self._stream.flush()
            except Exception:  # noqa: BLE001 — a broken stream must not crash work
                return
            self._frame += 1
            self._stop.wait(self._interval)


def live_status(label: str = "thinking…", **kwargs: object) -> LiveStatus:
    """Convenience factory mirroring the :class:`LiveStatus` constructor."""
    return LiveStatus(label, **kwargs)  # type: ignore[arg-type]
