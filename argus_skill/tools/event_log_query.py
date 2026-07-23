"""Thin CLI for querying one call from the canonical Argus event log."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ..life.event_log import event_log_paths, iter_call_events


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print JSONL event rows for one exact top-level call_id."
    )
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--call-id", required=True)
    args = parser.parse_args(argv)

    paths = event_log_paths(args.log)
    if not paths:
        print(f"event log not found: {args.log}", file=sys.stderr)
        return 2

    matched = 0
    try:
        for row in iter_call_events(args.log, args.call_id):
            print(json.dumps(row, ensure_ascii=False))
            matched += 1
    except (OSError, ValueError) as exc:
        print(f"event log query failed: {exc}", file=sys.stderr)
        return 2

    if matched == 0:
        print(
            f"no event rows found for call_id={args.call_id}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
