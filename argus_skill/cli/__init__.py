"""Terminal event rendering used by legacy Python CLI surfaces."""

from .render import render_event_for_terminal
from .theme import Theme, default_theme

__all__ = ["Theme", "default_theme", "render_event_for_terminal"]
