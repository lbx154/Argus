"""Read one agent call from a rotating Argus JSONL event log."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any


def event_log_paths(log_path: Path) -> list[Path]:
    """Return retained generations from oldest to newest."""
    path = Path(log_path)
    numbered: list[tuple[int, Path]] = []
    prefix = path.name + "."
    for candidate in path.parent.glob(prefix + "*"):
        suffix = candidate.name[len(prefix) :]
        if suffix.isdigit():
            numbered.append((int(suffix), candidate))

    older = [candidate for number, candidate in sorted(numbered) if number >= 2]
    newest_roll = next(
        (candidate for number, candidate in numbered if number == 1),
        None,
    )
    if newest_roll is not None:
        older.append(newest_roll)
    if path.exists():
        older.append(path)
    return older


def iter_call_events(log_path: Path, call_id: str) -> Iterator[dict[str, Any]]:
    """Yield rows whose top-level ``call_id`` exactly matches, oldest first."""
    target = str(call_id or "").strip()
    if not target:
        raise ValueError("call_id must be non-empty")

    matched_generations: list[list[dict[str, Any]]] = []
    for path in reversed(event_log_paths(Path(log_path))):
        generation_matches: list[dict[str, Any]] = []
        found_start = False
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                if not raw.endswith("\n"):
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid JSONL row at {path}:{line_number}: {exc}"
                    ) from exc
                if isinstance(row, dict) and str(row.get("call_id") or "") == target:
                    generation_matches.append(row)
                    found_start = found_start or row.get("type") == "agent.io.start"
        if generation_matches:
            matched_generations.append(generation_matches)
        if found_start:
            break

    for generation in reversed(matched_generations):
        yield from generation


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
