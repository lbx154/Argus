"""Report provider prompt-cache usage from an Argus project event log."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def _role(run_label: str) -> str:
    label = run_label.lower()
    for role in ("manager", "planner", "engineer", "reviewer"):
        if label == role or label.startswith((role + "-", role + ".")):
            return role
    return "other"


def summarize(path: Path) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"calls": 0, "known_calls": 0, "input_tokens": 0, "cached_tokens": 0}
    )
    with path.open(encoding="utf-8") as events:
        for line in events:
            event = json.loads(line)
            if event.get("type") != "usage.recorded":
                continue
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            input_tokens = event.get("input_tokens", usage.get("input_tokens"))
            cached_tokens = event.get(
                "cached_input_tokens", usage.get("cached_input_tokens")
            )
            key = (
                _role(str(event.get("run_label") or "")),
                str(event.get("provider") or "unknown"),
            )
            group = groups[key]
            group["calls"] += 1
            if input_tokens is None or cached_tokens is None:
                continue
            group["known_calls"] += 1
            group["input_tokens"] += max(0, int(input_tokens))
            group["cached_tokens"] += max(0, int(cached_tokens))
    return [
        {
            "role": role,
            "backend": backend,
            **values,
            "cache_hit_ratio": (
                values["cached_tokens"] / values["input_tokens"]
                if values["input_tokens"]
                else None
            ),
        }
        for (role, backend), values in sorted(groups.items())
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events_jsonl", type=Path)
    args = parser.parse_args()
    print("role\tbackend\tknown/total calls\tcached/input tokens\tcache-hit ratio")
    for row in summarize(args.events_jsonl):
        ratio = row["cache_hit_ratio"]
        rendered_ratio = "n/a" if ratio is None else f"{ratio:.2%}"
        print(
            f"{row['role']}\t{row['backend']}\t"
            f"{row['known_calls']}/{row['calls']}\t"
            f"{row['cached_tokens']}/{row['input_tokens']}\t{rendered_ratio}"
        )


if __name__ == "__main__":
    main()
