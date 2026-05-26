#!/usr/bin/env python3
"""Convenience wrapper for argus_skill.tools.new_auto_research_project."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _main() -> int:
    from argus_skill.tools.new_auto_research_project import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_main())
