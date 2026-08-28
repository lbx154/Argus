from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

if __package__:
    from .config import load_config
    from .criteria import evaluate
    from .patch import patch_stats
else:
    from config import load_config
    from criteria import evaluate
    from patch import patch_stats


def _annotation(message: str) -> str:
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("config.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    event = json.loads(args.event.read_text(encoding="utf-8"))
    pull = event["pull_request"]
    message = " ".join(filter(None, (pull.get("title"), pull.get("body"))))
    result = evaluate(
        message,
        patch_stats(pull["base"]["sha"], pull["head"]["sha"]),
        load_config(args.config),
    )
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    for error in result["errors"]:
        print(f"::error title=PR gate: {error['criterion']}::{_annotation(error['message'])}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(f"## PR gate: {result['status']}\n\n")
            for error in result["errors"]:
                handle.write(f"- **{error['criterion']}**: {error['message']}\n")

    return 2 if result["status"] == "incomplete" else 1 if result["status"] == "flagged" else 0


if __name__ == "__main__":
    raise SystemExit(main())
