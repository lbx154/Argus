"""``argus-skill`` brand assets — ASCII logo + startup banner.

The logo is a two-tone gradient (cyan → blue → magenta) ANSI Shadow
rendering of "argus-skill". A compact small-font fallback is used on
terminals narrower than the full logo's 84 columns.

``render_startup_banner(...)`` composes the logo + tagline + a small
status block (mission id, plan_mode, state-dir) — modelled on the
codex / skill-agent / claude-code interactive banners.
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


# ── ASCII art ─────────────────────────────────────────────────────────────

# Generated with pyfiglet ANSI Shadow font; 84 columns × 6 rows.
# DO NOT reflow — alignment is hand-tuned.
LOGO_FULL = r"""
 █████╗ ██████╗  ██████╗ ██╗   ██╗███████╗      ███████╗██╗  ██╗██╗██╗     ██╗
██╔══██╗██╔══██╗██╔════╝ ██║   ██║██╔════╝      ██╔════╝██║ ██╔╝██║██║     ██║
███████║██████╔╝██║  ███╗██║   ██║███████╗█████╗███████╗█████╔╝ ██║██║     ██║
██╔══██║██╔══██╗██║   ██║██║   ██║╚════██║╚════╝╚════██║██╔═██╗ ██║██║     ██║
██║  ██║██║  ██║╚██████╔╝╚██████╔╝███████║      ███████║██║  ██╗██║███████╗███████╗
╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝  ╚═════╝ ╚══════╝      ╚══════╝╚═╝  ╚═╝╚═╝╚══════╝╚══════╝
"""

# pyfiglet "small" font; 40 columns × 5 rows. Used when terminal width
# is below the full logo's 84 columns.
LOGO_COMPACT = r"""
                              _   _ _ _
 __ _ _ _ __ _ _  _ ______ __| |_(_) | |
/ _` | '_/ _` | || (_-<___(_-< / / | | |
\__,_|_| \__, |\_,_/__/   /__/_\_\_|_|_|
         |___/
"""

TAGLINE = "supervised skill-driven coding agent"


def _gradient_palette(theme: Theme) -> list[str]:
    """Return six ANSI methods on ``theme`` for the 6-row logo gradient."""
    return [
        "bold_cyan",
        "bold_cyan",
        "bold_blue",
        "bold_blue",
        "bold_magenta",
        "bold_magenta",
    ]


def render_logo(*, theme: Theme) -> str:
    """Render the logo, picking the variant that fits the terminal.

    On a 24-bit terminal the whole block gets one smooth mauve→blue→teal
    ramp shared across rows (each column the same hue, so the block letters
    read as a single gradient wordmark). On a basic colour TTY it falls back
    to the coarse per-row cyan→blue→mauve tri-tone.
    """
    full_lines = LOGO_FULL.strip("\n").splitlines()
    full_w = max(len(ln) for ln in full_lines)
    if theme.width >= full_w:
        lines = full_lines
    else:
        lines = LOGO_COMPACT.strip("\n").splitlines()

    if getattr(theme, "truecolor", False):
        # Smooth horizontal gradient shared across every row.
        block_w = max(len(ln) for ln in lines)
        return "\n".join(theme.gradient(ln, width=block_w) for ln in lines)

    # Non-truecolor: coarse per-row tri-tone (cyan → blue → mauve).
    palette = _gradient_palette(theme)
    out: list[str] = []
    for i, ln in enumerate(lines):
        method = palette[i % len(palette)]
        out.append(getattr(theme, method)(ln))
    return "\n".join(out)


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
        parts.append("")
        parts.append(
            "  " + theme.italic(theme.gray(TAGLINE)) +
            "  " + theme.dim(f"v{version}")
        )
        parts.append("")

    arrow = theme.dim("→")
    label = lambda s: theme.gray(f"{s:<11}")  # noqa: E731

    if mode == "mission" and mission_id:
        status_color = {
            "running": theme.bold_blue,
            "done": theme.bold_green,
            "error": theme.bold_red,
            "pending": theme.bold,
        }.get(mission_status or "", theme.bold)
        parts.append(
            f"  {label('mission')} {arrow} {theme.cyan(mission_id)}  "
            + status_color(mission_status or "?")
        )
        if plan_mode:
            parts.append(
                f"  {label('plan_mode')} {arrow} {theme.bold(plan_mode)}"
                + (f"   {theme.dim(f'max_rounds={max_rounds}')}" if max_rounds else "")
            )
        if auto_follow_up is not None:
            if auto_follow_up:
                # Highlighted in yellow — this is the dangerous knob.
                state_text = theme.bold(theme.yellow("on"))
                hint = theme.dim("(planner auto-spawns round N+1)")
            else:
                state_text = theme.bold_green("off")
                hint = theme.dim("(mission ends on first ✅ done)")
            parts.append(
                f"  {label('auto-follow')} {arrow} {state_text}   {hint}"
            )
        if objective:
            obj = objective.strip().splitlines()[0]
            if len(obj) > 100:
                obj = obj[:99] + "…"
            parts.append(f"  {label('objective')} {obj}")
    elif mode == "queue":
        parts.append(
            f"  {label('mode')} {arrow} {theme.bold('queue (worker)')}"
        )
    if state_dir:
        parts.append(f"  {label('state-dir')} {theme.cyan(state_dir)}")
    if daemon_pid is not None:
        parts.append(
            f"  {label('daemon')} {arrow} pid={daemon_pid}"
        )
    parts.append("")
    if show_hint:
        # Hint line — analogous to skill-agent.
        parts.append(
            "  " + theme.gray("type a command to begin  ·  ")
            + theme.cyan("/help") + theme.gray(" for commands  ·  ")
            + theme.cyan("/exit") + theme.gray(" to leave (daemon keeps running)")
        )
        parts.append("")
    return "\n".join(parts)
