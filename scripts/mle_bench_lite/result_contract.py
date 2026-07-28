#!/usr/bin/env python3
"""Build the durable result contract for one MLE-Bench campaign run.

The wrapper exit code is useful diagnostic evidence, but it is not stronger
than a Reviewer-approved medal for the exact current submission.  Keeping
that rule here prevents shell/deployment failures from erasing benchmark
success that the independent grader has already established.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def matching_medal_gate(
    project: Path, competition: str, submission: Path
) -> dict[str, Any] | None:
    """Return the gate only when it certifies the exact current submission."""
    if not submission.is_file() or submission.stat().st_size <= 0:
        return None
    gate = read_json_object(project / "MLE_MEDAL_GATE.json")
    if (
        gate.get("competition") != competition
        or gate.get("satisfied") is not True
        or gate.get("submission_sha256") != sha256_file(submission)
    ):
        return None
    return gate


def build_run_result(
    *,
    competition: str,
    slot: int,
    argus_exit_code: int,
    grade_exit_code: int,
    submission: Path,
    grade_log: Path,
    elapsed_seconds: int,
    now: float | None = None,
) -> dict[str, Any]:
    project = submission.parent
    medal_gate = matching_medal_gate(project, competition, submission)
    timed_benchmark_complete = argus_exit_code in {0, 124} or elapsed_seconds >= 3600
    benchmark_complete = medal_gate is not None or timed_benchmark_complete
    if medal_gate is not None:
        completion_reason = "reviewer_approved_medal"
    elif argus_exit_code == 0:
        completion_reason = "argus_clean_exit"
    elif argus_exit_code == 124:
        completion_reason = "task_timeout"
    elif elapsed_seconds >= 3600:
        completion_reason = "minimum_benchmark_runtime"
    else:
        completion_reason = "infrastructure_failure"
    return {
        "competition": competition,
        "slot": slot,
        "argus_exit_code": argus_exit_code,
        "grade_exit_code": grade_exit_code,
        "submission_exists": submission.is_file() and submission.stat().st_size > 0,
        "submission_sha256": sha256_file(submission) if submission.is_file() else None,
        "grade_log": str(grade_log),
        "finished_at": now if now is not None else time.time(),
        "elapsed_seconds": elapsed_seconds,
        "benchmark_complete": benchmark_complete,
        "completion_reason": completion_reason,
        "medal_gate_satisfied": medal_gate is not None,
        "medal_gate": medal_gate,
        "retryable_infrastructure_failure": not benchmark_complete,
    }


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("competition")
    parser.add_argument("slot", type=int)
    parser.add_argument("argus_exit_code", type=int)
    parser.add_argument("grade_exit_code", type=int)
    parser.add_argument("submission", type=Path)
    parser.add_argument("grade_log", type=Path)
    parser.add_argument("elapsed_seconds", type=int)
    args = parser.parse_args()
    atomic_json(
        args.result,
        build_run_result(
            competition=args.competition,
            slot=args.slot,
            argus_exit_code=args.argus_exit_code,
            grade_exit_code=args.grade_exit_code,
            submission=args.submission,
            grade_log=args.grade_log,
            elapsed_seconds=args.elapsed_seconds,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
