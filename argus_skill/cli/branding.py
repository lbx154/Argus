"""``argus-skill`` brand assets — wordmark logo + startup banner.

The logo is a single-line wordmark: a geometric accent glyph plus the
lowercase "argus" wordmark carried on the shared mauve→blue→teal gradient
(``theme.gradient``). This deliberately replaced an 84×6 ANSI-Shadow
figlet block — a small, restrained wordmark reads as modern where the 3-D
block art read as dated. On a basic colour TTY the wordmark degrades to
the single signature mauve; with colour off it is plain text.

``render_startup_banner(...)`` composes the wordmark + a dim tagline·version
subtitle + a small status block (mission id, plan_mode, state-dir) —
secondary info is dim, state is carried by a coloured ``●`` dot, and there
are no ``label → value`` arrow rows. Modelled on the codex / claude-code
interactive banners.
"""

from __future__ import annotations

from .theme import Theme

__all__ = [
    "LOGO_FULL",
    "LOGO_COMPACT",
    "TAGLINE",
    "render_logo",
    "render_startup_banner",
]


# ── wordmark ──────────────────────────────────────────────────────────────

# Signature accent glyph + lowercase wordmark. Kept narrow and unambiguous;
# the mark is printed once at startup (not in any cursor-math redraw), so a
# decorative glyph here never affects the live-panel row math.
_LOGO_GLYPH = "◆"
_WORDMARK = "argus"

# ``LOGO_FULL`` / ``LOGO_COMPACT`` are retained (exported via ``cli.__init__``)
# but are now the plain wordmark text, no longer 6-row figlet art. FULL carries
# the accent glyph; COMPACT drops it for very narrow terminals.
LOGO_FULL = f"{_LOGO_GLYPH} {_WORDMARK}"
LOGO_COMPACT = _WORDMARK

TAGLINE = "supervised skill-driven coding agent"


def render_logo(*, theme: Theme) -> str:
    """Render the one-line wordmark, indented to the banner's column-2 grid.

    Truecolor: the wordmark rides the shared mauve→blue→teal ramp and the
    glyph is the signature mauve. Basic TTY: both collapse to bold mauve.
    Colour off: plain ``◆ argus``. Always a single row.
    """
    # Drop the glyph only on a genuinely tiny terminal; the wordmark itself is
    # 5 columns and always fits.
    compact = theme.width < 24

    if not theme.enabled:
        return "  " + (LOGO_COMPACT if compact else LOGO_FULL)

    word = theme.gradient(_WORDMARK)
    if compact:
        return "  " + word
    return "  " + theme.magenta(_LOGO_GLYPH) + " " + word


def render_startup_banner(
    *,
    theme: Theme,
    version: str,
    mode: str | None = None,           # "mission" | "queue"
    mission_id: str | None = None,
    mission_status: str | None = None,
    plan_mode: str | None = None,
    auto_follow_up: bool | None = None,
    objective: str | None = None,
    state_dir: str | None = None,
    daemon_pid: int | None = None,
    max_rounds: int | None = None,
    show_logo: bool = True,
    show_hint: bool = True,
) -> str:
    """Compose the full startup banner (logo + tagline + status block).

    All status fields are optional; only the lines that have values are
    rendered. Designed to be called once at the top of ``argus-skill go``
    or ``argus-skill chat`` — *replaces* the previous flat text header.

    Pass ``show_logo=False`` to render only the status block (used when
    the logo has already been shown earlier in the same session, e.g.
    ``argus-skill go`` shows the logo before the objective prompt and
    then opens the chat REPL which only needs to show the status block).
    """
    parts: list[str] = []
    if show_logo:
        parts.append(render_logo(theme=theme))
        # Dim tagline · version subtitle (secondary text, CC-style) — one row
        # right under the wordmark, aligned to the same column-2 grid.
        parts.append("  " + theme.dim(f"{TAGLINE}  ·  v{version}"))
        parts.append("")

    # One status row = "  <dim label>  <value>" — no ``→`` arrows, no fixed
    # 11-col label padding; state is carried by a coloured ``●`` dot.
    def _row(label: str, value: str) -> str:
        return f"  {theme.gray(label)}  {value}"

    if mode == "mission" and mission_id:
        dot_color = {
            "running": theme.cyan,
            "done": theme.green,
            "error": theme.red,
            "pending": theme.yellow,
        }.get(mission_status or "", theme.gray)
        status_dot = dot_color("● " + (mission_status or "?"))
        parts.append(_row("mission", theme.cyan(mission_id) + "   " + status_dot))
        if plan_mode:
            extra = (
                f"   {theme.dim(f'max_rounds={max_rounds}')}" if max_rounds else ""
            )
            parts.append(_row("plan", theme.bold(plan_mode) + extra))
        if auto_follow_up is not None:
            if auto_follow_up:
                # Highlighted in yellow — this is the dangerous knob.
                state_text = theme.bold(theme.yellow("● on"))
                hint = theme.dim("(planner auto-spawns round N+1)")
            else:
                state_text = theme.bold_green("● off")
                hint = theme.dim("(mission ends on first ✅ done)")
            parts.append(_row("auto-follow", f"{state_text}   {hint}"))
        if objective:
            obj = objective.strip().splitlines()[0]
            if len(obj) > 100:
                obj = obj[:99] + "…"
            parts.append(_row("objective", obj))
    elif mode == "queue":
        parts.append(_row("mode", theme.bold("queue (worker)")))
    if state_dir:
        parts.append(_row("state-dir", theme.cyan(state_dir)))
    if daemon_pid is not None:
        parts.append(_row("daemon", theme.dim(f"pid={daemon_pid}")))
    parts.append("")
    if show_hint:
        # Hint line — analogous to skill-agent.
        parts.append(
            "  " + theme.gray("type a command to begin  ·  ")
            + theme.cyan("/help") + theme.gray(" commands  ·  ")
            + theme.cyan("/exit") + theme.gray(" to leave (daemon keeps running)")
        )
        parts.append("")
    return "\n".join(parts)
