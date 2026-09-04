from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

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


def _pull_request_input(event_path: Path) -> tuple[str, str, str]:
    try:
        event = json.loads(event_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read event payload: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"event payload is not valid JSON: {exc.msg}") from exc
    if not isinstance(event, Mapping):
        raise ValueError("event payload must be a JSON object")

    pull = event.get("pull_request")
    if not isinstance(pull, Mapping):
        raise ValueError("event payload is missing pull_request")
    title = pull.get("title")
    body = pull.get("body")
    if title is not None and not isinstance(title, str):
        raise ValueError("pull_request.title must be a string or null")
    if body is not None and not isinstance(body, str):
        raise ValueError("pull_request.body must be a string or null")

    revisions: dict[str, str] = {}
    for name in ("base", "head"):
        revision = pull.get(name)
        if not isinstance(revision, Mapping):
            raise ValueError(f"pull_request.{name} must be an object")
        sha = revision.get("sha")
        if not isinstance(sha, str) or not sha.strip():
            raise ValueError(f"pull_request.{name}.sha must be a non-empty string")
        revisions[name] = sha

    message = " ".join(value for value in (title, body) if value)
    return message, revisions["base"], revisions["head"]


def _error_result(message: str) -> dict[str, Any]:
    return {
        "schema_version": "pr-gate/1.0",
        "status": "error",
        "errors": [{"criterion": "input", "message": message}],
        "unavailable_criteria": [],
        "patch": None,
        "criteria": {},
    }


def _emit_result(result: dict[str, Any], output: Path) -> None:
    rendered = json.dumps(result, indent=2, sort_keys=True)
    output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    for error in result["errors"]:
        print(f"::error title=PR gate: {error['criterion']}::{_annotation(error['message'])}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(f"## PR gate: {result['status']}\n\n")
            for error in result["errors"]:
                handle.write(f"- **{error['criterion']}**: {error['message']}\n")


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

    try:
        message, base_sha, head_sha = _pull_request_input(args.event)
    except ValueError as exc:
        _emit_result(_error_result(str(exc)), args.output)
        return 2

    result = evaluate(
        message,
        patch_stats(base_sha, head_sha),
        load_config(args.config),
    )
    _emit_result(result, args.output)

    return 2 if result["status"] == "incomplete" else 1 if result["status"] == "flagged" else 0


if __name__ == "__main__":
    raise SystemExit(main())
