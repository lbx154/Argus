"""Amdahl-style leverage gate for kernel optimization attempts.

The cheapest way to avoid wasting a kernel iteration is to prove that the
selected kernel can move the end-to-end metric enough to clear measurement
noise before editing source or collecting an expensive second profile.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
VERDICTS = frozenset({
    "proceed",
    "reject_low_leverage_ceiling",
    "reject_insufficient_plausible_gain",
})


def _positive(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) > 0


def analyze_leverage(
    *,
    attempt_id: str,
    baseline_identity: str,
    path_coverage: str,
    evidence: str,
    end_to_end_ms: float,
    target_kernel_ms: float,
    required_total_speedup: float,
    plausible_kernel_speedup: float,
) -> dict[str, Any]:
    if not _positive(end_to_end_ms):
        raise ValueError("end_to_end_ms must be positive")
    if not _positive(target_kernel_ms) or target_kernel_ms > end_to_end_ms:
        raise ValueError("target_kernel_ms must be positive and <= end_to_end_ms")
    if not _positive(required_total_speedup) or required_total_speedup <= 1:
        raise ValueError("required_total_speedup must be > 1")
    if not _positive(plausible_kernel_speedup) or plausible_kernel_speedup < 1:
        raise ValueError("plausible_kernel_speedup must be >= 1")

    share = target_kernel_ms / end_to_end_ms
    max_total_speedup = 1.0 / (1.0 - share) if share < 1 else math.inf
    predicted_total_speedup = 1.0 / (
        (1.0 - share) + share / plausible_kernel_speedup
    )
    denominator = (1.0 / required_total_speedup) - (1.0 - share)
    required_kernel_speedup = share / denominator if denominator > 0 else None

    if max_total_speedup + 1e-12 < required_total_speedup:
        verdict = "reject_low_leverage_ceiling"
    elif predicted_total_speedup + 1e-12 < required_total_speedup:
        verdict = "reject_insufficient_plausible_gain"
    else:
        verdict = "proceed"

    return {
        "schema_version": SCHEMA_VERSION,
        "attempt_id": str(attempt_id).strip(),
        "baseline_identity": str(baseline_identity).strip(),
        "path_coverage": str(path_coverage).strip(),
        "evidence": str(evidence).strip(),
        "end_to_end_ms": float(end_to_end_ms),
        "target_kernel_ms": float(target_kernel_ms),
        "target_share": share,
        "required_total_speedup": float(required_total_speedup),
        "plausible_kernel_speedup": float(plausible_kernel_speedup),
        "predicted_total_speedup": predicted_total_speedup,
        "theoretical_max_total_speedup": max_total_speedup,
        "required_kernel_speedup": required_kernel_speedup,
        "verdict": verdict,
    }


def validate_leverage(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {record.get('schema_version')!r}")
    for key in ("attempt_id", "baseline_identity", "path_coverage", "evidence"):
        if not isinstance(record.get(key), str) or not str(record.get(key)).strip():
            errors.append(f"{key} is empty")
    if record.get("verdict") not in VERDICTS:
        errors.append(f"invalid verdict: {record.get('verdict')!r}")
    if errors:
        return errors
    try:
        expected = analyze_leverage(
            attempt_id=str(record["attempt_id"]),
            baseline_identity=str(record["baseline_identity"]),
            path_coverage=str(record["path_coverage"]),
            evidence=str(record["evidence"]),
            end_to_end_ms=float(record["end_to_end_ms"]),
            target_kernel_ms=float(record["target_kernel_ms"]),
            required_total_speedup=float(record["required_total_speedup"]),
            plausible_kernel_speedup=float(record["plausible_kernel_speedup"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return [f"invalid leverage inputs: {exc}"]
    for key in (
        "target_share",
        "predicted_total_speedup",
        "theoretical_max_total_speedup",
    ):
        try:
            actual = float(record[key])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{key} is missing or invalid")
            continue
        if not math.isclose(actual, float(expected[key]), rel_tol=1e-9, abs_tol=1e-12):
            errors.append(f"{key} does not match recomputed value")
    expected_required = expected["required_kernel_speedup"]
    actual_required = record.get("required_kernel_speedup")
    if expected_required is None:
        if actual_required is not None:
            errors.append("required_kernel_speedup must be null when target cannot meet the gate")
    else:
        try:
            if not math.isclose(
                float(actual_required),
                float(expected_required),
                rel_tol=1e-9,
                abs_tol=1e-12,
            ):
                errors.append("required_kernel_speedup does not match recomputed value")
        except (TypeError, ValueError):
            errors.append("required_kernel_speedup is missing or invalid")
    if record.get("verdict") != expected["verdict"]:
        errors.append("verdict does not match recomputed leverage decision")
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--attempt-id", required=True)
    analyze.add_argument("--baseline-identity", required=True)
    analyze.add_argument("--path-coverage", required=True)
    analyze.add_argument("--evidence", required=True)
    analyze.add_argument("--end-to-end-ms", required=True, type=float)
    analyze.add_argument("--target-kernel-ms", required=True, type=float)
    analyze.add_argument("--required-total-speedup", required=True, type=float)
    analyze.add_argument("--plausible-kernel-speedup", required=True, type=float)
    analyze.add_argument("--output", type=Path)
    check = sub.add_parser("check-file")
    check.add_argument("path", type=Path)
    check_all = sub.add_parser("check")
    check_all.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "analyze":
        try:
            record = analyze_leverage(
                attempt_id=args.attempt_id,
                baseline_identity=args.baseline_identity,
                path_coverage=args.path_coverage,
                evidence=args.evidence,
                end_to_end_ms=args.end_to_end_ms,
                target_kernel_ms=args.target_kernel_ms,
                required_total_speedup=args.required_total_speedup,
                plausible_kernel_speedup=args.plausible_kernel_speedup,
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        rendered = json.dumps(record, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 0

    paths = [args.path] if args.command == "check-file" else sorted(
        path
        for dirname in ("attempts", "experiments")
        for path in (args.project_root / dirname).rglob("LEVERAGE.json")
        if path.is_file()
    )
    if not paths:
        print("ERROR: no attempts/**/LEVERAGE.json evidence found", file=sys.stderr)
        return 2
    failed = False
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            print(f"ERROR: {path}: {exc}", file=sys.stderr)
            failed = True
            continue
        if not isinstance(record, dict):
            print(f"ERROR: {path}: leverage record must be a JSON object", file=sys.stderr)
            failed = True
            continue
        errors = validate_leverage(record)
        for error in errors:
            print(f"ERROR: {path}: {error}", file=sys.stderr)
        failed = failed or bool(errors)
    if failed:
        return 2
    print(f"kernel leverage gates: {len(paths)} valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
