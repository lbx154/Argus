"""Check the separation between execution failures and idea evidence.

The four-state model and its invariants live in
:mod:`argus_skill.core.evidence_status`; this module supplies the GPU-kernel
vocabulary — profiler and benchmark-infrastructure failures, and the
commit/diff/dispatch identity a performance claim needs before anyone can
check it. Behaviour is unchanged: the rules moved, they did not loosen.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ...core.evidence_status import (
    BASE_FAILURE_CLASSES,
    BASE_NON_IDEA_FAILURES,
    EXECUTION_STATUSES,
    IDEA_STATUSES,
    EvidenceContract,
    is_placeholder_text,
    validate_evidence,
)

SCHEMA_VERSION = 1

# Kernel work fails in ways generic domains do not: a profiler the host
# refuses to run, a benchmark harness that never dispatched into the changed
# kernel. None of them say anything about the optimization idea.
_KERNEL_ONLY_FAILURES = frozenset(
    {
        "profiler_permission",
        "benchmark_infrastructure",
        "measurement_infrastructure",
        "numerical",
        "performance",
    }
)
FAILURE_CLASSES = BASE_FAILURE_CLASSES | _KERNEL_ONLY_FAILURES
NON_IDEA_FAILURES = BASE_NON_IDEA_FAILURES | frozenset(
    {
        "profiler_permission",
        "benchmark_infrastructure",
        "measurement_infrastructure",
    }
)

KERNEL_EVIDENCE = EvidenceContract(
    domain="kernel",
    failure_classes=FAILURE_CLASSES,
    non_idea_failures=NON_IDEA_FAILURES,
    # A speedup claim is unfalsifiable without knowing what was compared and
    # whether the benchmark reached the code that changed.
    grounding_fields=("baseline_identity", "candidate_identity", "path_coverage"),
    # Only a real measurement can refute a kernel idea; a build error cannot.
    refuting_failures=frozenset({"numerical", "performance"}),
)

__all__ = [
    "EXECUTION_STATUSES",
    "FAILURE_CLASSES",
    "IDEA_STATUSES",
    "KERNEL_EVIDENCE",
    "NON_IDEA_FAILURES",
    "SCHEMA_VERSION",
    "main",
    "outcome_files",
    "template",
    "validate_outcome",
]


def validate_outcome(record: dict[str, Any]) -> list[str]:
    """Kernel-specific checks, then the shared four-state invariants."""
    errors: list[str] = []
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {record.get('schema_version')!r}")
    if is_placeholder_text(record.get("attempt_id")):
        errors.append("attempt_id is empty or templated")
    shapes = record.get("benchmark_shapes")
    if shapes is not None:
        if not isinstance(shapes, dict):
            errors.append("benchmark_shapes must map shape ids to dimension objects")
        else:
            from .benchmark_preflight import preflight_shape

            for shape_id, shape in shapes.items():
                if not isinstance(shape, dict):
                    errors.append(f"benchmark shape {shape_id!r} is not an object")
                    continue
                try:
                    result = preflight_shape(
                        str(shape_id),
                        shape,
                        dtype=str(record.get("benchmark_dtype") or "bf16"),
                    )
                except (TypeError, ValueError) as exc:
                    errors.append(f"benchmark shape {shape_id!r}: {exc}")
                    continue
                if not result.declared:
                    errors.append(
                        f"benchmark shape {shape_id!r}: id has no parseable dimensions"
                    )
                errors.extend(
                    f"benchmark shape {shape_id!r}: {mismatch}"
                    for mismatch in result.mismatches
                )
    errors.extend(validate_evidence(record, KERNEL_EVIDENCE))
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
        "evidence": "REPLACE with evidence file paths or exact command result",
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
