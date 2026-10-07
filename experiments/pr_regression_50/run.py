"""Compatibility entry point; implementation lives in the PR gate package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from argus.release_tools.pr_gate.regression.study import main

if __name__ == "__main__":
    main()
