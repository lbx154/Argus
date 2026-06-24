from __future__ import annotations

import sys

from .orchestrator import _main

if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
