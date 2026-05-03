"""``python -m argus_skill`` entry point and the [project.scripts] target.

The entry point declared in pyproject.toml is
``argus_skill.__main__:main`` — we re-export ``main`` from
``apps.cli`` here so that resolves correctly.
"""
from __future__ import annotations

import sys

from .apps.cli import main

if __name__ == "__main__":
    sys.exit(main())
