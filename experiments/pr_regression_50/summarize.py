"""Compatibility entry point for the packaged historical artifact summarizer."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from argus.release_tools.pr_gate.regression.study_summary import main

if __name__ == "__main__":
    main()
