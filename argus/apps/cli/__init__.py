"""Public entry points for the internal administration CLI."""
from __future__ import annotations

from ._core import main
from ._parser import build_parser

__all__ = ["build_parser", "main"]
