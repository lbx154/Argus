"""Single-threaded exec boundary for detached daemon spawning."""

from __future__ import annotations

import json
import sys

from .config import config_from_payload
from .life_worker import spawn_detached_daemon


def main() -> int:
    config = config_from_payload(json.load(sys.stdin))
    return spawn_detached_daemon(config, quiet=True)


if __name__ == "__main__":
    raise SystemExit(main())
