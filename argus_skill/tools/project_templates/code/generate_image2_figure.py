"""Compatibility wrapper for AGENTS.md references to generate_image2_figure.py."""
from __future__ import annotations

from generate_image_2 import main  # type: ignore[import-not-found]

if __name__ == "__main__":
    raise SystemExit(main())
