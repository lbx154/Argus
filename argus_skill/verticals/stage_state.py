"""Structured pipeline-state checks used by vertical stage gates."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


class StageStateError(ValueError):
    """Raised when persisted pipeline state does not satisfy a structural gate."""


def validate_stage_status(
    project_root: Path,
    *,
    stage: str,
    allowed_statuses: set[str],
) -> Path:
    """Validate one stage's exact persisted status."""
    root = project_root.resolve()
    candidate = root / "research" / "PIPELINE_STATE.json"
    try:
        path = candidate.resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise StageStateError(f"{candidate}: file resolves outside the project")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except StageStateError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise StageStateError(f"{candidate}: invalid pipeline state: {exc}") from exc
    if not isinstance(payload, dict):
        raise StageStateError(f"{path}: expected a JSON object")
    stages = payload.get("stages")
    if not isinstance(stages, dict):
        raise StageStateError(f"{path}: stages must be an object")
    state = stages.get(stage)
    if not isinstance(state, dict):
        raise StageStateError(f"{path}: stage {stage!r} is missing")
    status = str(state.get("status") or "").strip().lower()
    if status not in allowed_statuses:
        expected = ", ".join(sorted(allowed_statuses))
        raise StageStateError(
            f"{path}: stage {stage!r} has status {status!r}; expected one of {expected}"
        )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="argus-stage-state")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--stage", required=True)
    parser.add_argument("--allow", action="append", required=True)
    args = parser.parse_args(argv)
    allowed = {str(value).strip().lower() for value in args.allow if str(value).strip()}
    try:
        path = validate_stage_status(
            Path(args.project_root).resolve(),
            stage=str(args.stage).strip().lower(),
            allowed_statuses=allowed,
        )
    except StageStateError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"OK: validated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
