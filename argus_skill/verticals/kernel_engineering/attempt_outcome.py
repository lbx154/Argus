"""Validate the separation between execution failures and idea evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
EXECUTION_STATUSES = frozenset({"completed", "blocked", "failed"})
IDEA_STATUSES = frozenset({"untested", "inconclusive", "supported", "refuted"})
FAILURE_CLASSES = frozenset(
    {
        "none",
        "environment",
        "dependency",
        "toolchain",
        "build_configuration",
        "hardware_access",
        "profiler_permission",
        "benchmark_infrastructure",
        "measurement_infrastructure",
        "implementation",
        "numerical",
        "performance",
    }
)
NON_IDEA_FAILURES = frozenset(
    {
        "environment",
        "dependency",
        "toolchain",
        "build_configuration",
        "hardware_access",
        "profiler_permission",
        "benchmark_infrastructure",
        "measurement_infrastructure",
    }
)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "REPLACE" not in value


def validate_outcome(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {record.get('schema_version')!r}")
    if not _text(record.get("attempt_id")):
        errors.append("attempt_id is empty or templated")
    execution = str(record.get("execution_status") or "")
    failure = str(record.get("failure_class") or "")
    idea = str(record.get("idea_status") or "")
    if execution not in EXECUTION_STATUSES:
        errors.append(f"invalid execution_status: {execution!r}")
    if failure not in FAILURE_CLASSES:
        errors.append(f"invalid failure_class: {failure!r}")
    if idea not in IDEA_STATUSES:
        errors.append(f"invalid idea_status: {idea!r}")
    for key in ("summary", "evidence"):
        if not _text(record.get(key)):
            errors.append(f"{key} is empty or templated")

    if failure in NON_IDEA_FAILURES and idea not in {"untested", "inconclusive"}:
        errors.append(
            f"{failure} is an execution/environment failure; idea_status must be "
            "untested or inconclusive"
        )
    if execution != "completed" and idea in {"supported", "refuted"}:
        errors.append(f"execution_status={execution} cannot support or refute the idea")
    if idea == "refuted" and failure not in {"numerical", "performance"}:
        errors.append(
            "idea_status=refuted requires a completed valid numerical or performance result"
        )
    if idea in {"supported", "refuted"}:
        for key in ("baseline_identity", "candidate_identity", "path_coverage"):
            if not _text(record.get(key)):
                errors.append(
                    f"{key} is required before an idea can be {idea}; include commit/"
                    "diff identity and evidence that the measured case exercised the "
                    "changed path"
                )
    if failure == "none" and execution != "completed":
        errors.append("failure_class=none requires execution_status=completed")
    return list(dict.fromkeys(errors))


def template(attempt_id: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "execution_status": "blocked",
        "failure_class": "environment",
        "idea_status": "untested",
        "baseline_identity": "REPLACE with baseline revision/environment identity",
        "candidate_identity": "REPLACE with candidate commit plus dirty diff hash",
        "path_coverage": "REPLACE with dispatch/trace evidence for the changed path",
        "summary": "REPLACE with the concise observed outcome",
        "evidence": "REPLACE with artifact paths or exact command result",
    }


def outcome_files(project_root: Path) -> list[Path]:
    paths: list[Path] = []
    for dirname in ("attempts", "experiments"):
        root = project_root / dirname
        if root.is_dir():
            paths.extend(root.rglob("OUTCOME.json"))
    return sorted(set(paths))


def _read(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("template")
    show.add_argument("--attempt-id", required=True)
    check = sub.add_parser("check")
    check.add_argument("--project-root", type=Path, default=Path.cwd())
    check_file = sub.add_parser("check-file")
    check_file.add_argument("path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "template":
        print(json.dumps(template(args.attempt_id), indent=2, sort_keys=True))
        return 0
    paths = [args.path] if args.command == "check-file" else outcome_files(args.project_root)
    if not paths:
        print("no attempts/**/OUTCOME.json evidence found", file=sys.stderr)
        return 2
    failed = False
    for path in paths:
        record = _read(path)
        if record is None:
            print(f"ERROR: {path}: unreadable outcome JSON", file=sys.stderr)
            failed = True
            continue
        errors = validate_outcome(record)
        for error in errors:
            print(f"ERROR: {path}: {error}", file=sys.stderr)
        failed = failed or bool(errors)
    if failed:
        return 2
    print(f"kernel attempt outcomes: {len(paths)} valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
