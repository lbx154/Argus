"""Terminal UX helpers — theming, event rendering, banners.

Used by the chat REPL (`argus-skill chat`) and `argus-skill go`.
The daemon side stays untouched — it emits raw structured events to
JSONL outbox; only this layer turns them into pretty terminal output.
"""

from .branding import (
    LOGO_COMPACT,
    LOGO_FULL,
    TAGLINE,
    render_logo,
    render_startup_banner,
)
from .render import render_event_for_terminal, render_welcome_banner
from .theme import Theme, default_theme

__all__ = [
    "Theme",
    "default_theme",
    "render_event_for_terminal",
    "render_welcome_banner",
    "LOGO_FULL",
    "LOGO_COMPACT",
    "TAGLINE",
    "render_logo",
    "render_startup_banner",
]
