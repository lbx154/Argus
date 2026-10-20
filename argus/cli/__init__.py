"""Terminal presentation helpers for headless runtimes.

Layer: capabilities
"""

from .render import render_event_for_terminal
from .theme import Theme, default_theme

__all__ = ["Theme", "default_theme", "render_event_for_terminal"]
