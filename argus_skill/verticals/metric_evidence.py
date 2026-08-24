"""Structured result evidence for metric optimization verticals."""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections.abc import Mapping
from pathlib import Path


class EvidenceError(ValueError):
    """Raised when no trustworthy scored row is present."""


_SPEEDRUN_METRICS = {
    "val_bpb",
    "mean_val_bpb",
    "seconds_to_target",
    "wall_seconds",
    "score",
    "metric_value",
}
_NANOGPT_TIMING_METRICS = {"seconds_to_target", "wall_seconds"}
_NANOGPT_LOSS_FIELDS = {"val_loss", "final_val_loss"}
_CORRECT_FIELDS = ("correct", "is_correct")
_SOL_FIELDS = ("sol_pct", "sol", "sol_score")
_MATH_SYNTH_METRICS = {"score", "pass_gap", "mean_pass_gap"}
_TRUE_VALUES = {"1", "true", "yes", "pass", "passed", "correct"}


def _finite_number(value: object) -> float | None:
    try:
        number = float(str(value).strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _csv_rows(path: Path) -> tuple[set[str], list[dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = {str(name or "").strip().lower() for name in (reader.fieldnames or [])}
            rows = [
                {str(key or "").strip().lower(): str(value or "").strip() for key, value in row.items()}
                for row in reader
            ]
    except (OSError, csv.Error) as exc:
        raise EvidenceError(f"{path}: unreadable CSV: {exc}") from exc
    return fields, rows


def _candidate_csvs(project_root: Path, pattern: str) -> list[Path]:
    root = project_root.resolve()
    paths: set[Path] = set()
    for rel in ("attempts", "experiments"):
        base = root / rel
        if base.is_dir():
            paths.update(
                path
                for path in base.rglob(pattern)
                if _is_contained_file(root, path)
            )
    return sorted(paths)


def _is_contained_file(project_root: Path, path: Path) -> bool:
    root = project_root.resolve()
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    return resolved.is_relative_to(root) and resolved.is_file()


def _normalized_object(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return {str(key).strip().lower(): value for key, value in payload.items()}


def _has_finite_metric(
    payload: dict[str, object],
    metric_fields: set[str],
) -> bool:
    nested = payload.get("metrics")
    if isinstance(nested, dict):
        payload = {
            **payload,
            **{str(key).strip().lower(): value for key, value in nested.items()},
        }
    return any(_finite_number(payload.get(field)) is not None for field in metric_fields)


def validate_speedrun_evidence(project_root: Path) -> Path:
    """Return a results CSV containing at least one finite metric value."""
    for path in _candidate_csvs(project_root, "results.csv"):
        fields, rows = _csv_rows(path)
        metric_fields = fields & _SPEEDRUN_METRICS
        if metric_fields and any(
            _finite_number(row.get(field)) is not None
            for row in rows
            for field in metric_fields
        ):
            return path
    raise EvidenceError(
        "no attempts/**/results.csv or experiments/**/results.csv contains a "
        f"finite metric column ({', '.join(sorted(_SPEEDRUN_METRICS))})"
    )


def validate_nanogpt_evidence(project_root: Path) -> Path:
    """Return a real 8xH100 result that reached the fixed 3.28 loss target."""
    for path in _candidate_csvs(project_root, "results.csv"):
        fields, rows = _csv_rows(path)
        timing_fields = fields & _NANOGPT_TIMING_METRICS
        loss_fields = fields & _NANOGPT_LOSS_FIELDS
        for row in rows:
            timings = [
                number
                for field in timing_fields
                if (number := _finite_number(row.get(field))) is not None
            ]
            losses = [
                number
                for field in loss_fields
                if (number := _finite_number(row.get(field))) is not None
            ]
            gpu_count = _finite_number(row.get("gpu_count"))
            gpu_model = str(row.get("gpu_model") or "").strip().lower()
            if (
                timings
                and all(number >= 0 for number in timings)
                and losses
                and min(losses) <= 3.28
                and gpu_count == 8
                and "h100" in gpu_model
            ):
                return path
    raise EvidenceError(
        "no results row proves a finite non-negative seconds_to_target, "
        "val_loss<=3.28, gpu_count=8, and gpu_model=H100"
    )


def validate_speedrun_reference(project_root: Path) -> Path:
    """Return a structured reference artifact with a finite baseline metric."""
    reference_root = project_root / "reference"
    if reference_root.is_dir():
        for path in sorted(reference_root.rglob("*.csv")):
            if not _is_contained_file(project_root, path):
                continue
            fields, rows = _csv_rows(path)
            metric_fields = fields & _SPEEDRUN_METRICS
            if metric_fields and any(
                _finite_number(row.get(field)) is not None
                for row in rows
                for field in metric_fields
            ):
                return path
    for rel in (
        "research/REFERENCE_SCORES.json",
        "research/GROUND_TRUTH.json",
    ):
        path = project_root / rel
        if not _is_contained_file(project_root, path):
            continue
        payload = _normalized_object(path)
        if payload is not None and _has_finite_metric(payload, _SPEEDRUN_METRICS):
            return path
    raise EvidenceError(
        "no reference/**/*.csv, research/REFERENCE_SCORES.json, or "
        "research/GROUND_TRUTH.json contains a finite speedrun metric"
    )


def _kernel_result_row(row: Mapping[str, object]) -> bool:
    normalized = {str(key).strip().lower(): value for key, value in row.items()}
    correct_values = [
        str(normalized[field]).strip().lower() in _TRUE_VALUES
        for field in _CORRECT_FIELDS
        if field in normalized
    ]
    if not correct_values or len(set(correct_values)) != 1 or not correct_values[0]:
        return False
    sol_values: list[float] = []
    for field in _SOL_FIELDS:
        if field not in normalized or str(normalized[field]).strip() == "":
            continue
        number = _finite_number(normalized[field])
        if number is None:
            return False
        sol_values.append(number)
    if not sol_values or any(value < 0 for value in sol_values):
        return False
    return all(
        math.isclose(value, sol_values[0], rel_tol=1e-9, abs_tol=1e-12)
        for value in sol_values[1:]
    )


def validate_kernelbench_evidence(project_root: Path) -> Path:
    """Return a CSV containing a correctness-gated finite SOL row."""
    for path in _candidate_csvs(project_root, "*.csv"):
        fields, rows = _csv_rows(path)
        correct_fields = fields & set(_CORRECT_FIELDS)
        sol_fields = fields & set(_SOL_FIELDS)
        if not correct_fields or not sol_fields:
            continue
        if any(_kernel_result_row(row) for row in rows):
            return path
    for rel in ("attempts", "experiments"):
        base = project_root.resolve() / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.json")):
            if not _is_contained_file(project_root, path):
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rows = payload if isinstance(payload, list) else [payload]
            if any(isinstance(row, dict) and _kernel_result_row(row) for row in rows):
                return path
        for path in sorted(base.rglob("*.jsonl")):
            if not _is_contained_file(project_root, path):
                continue
            try:
                rows = [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except (OSError, json.JSONDecodeError):
                continue
            if any(isinstance(row, dict) and _kernel_result_row(row) for row in rows):
                return path
    raise EvidenceError(
        "no attempts/** or experiments/** CSV/JSON/JSONL result contains a row "
        "with correct=true and a finite non-negative sol_pct"
    )


def validate_math_synth_evidence(project_root: Path) -> Path:
    """Return an attempt summary containing a finite synthesized-problem score."""
    candidates: set[Path] = set()
    for rel in ("attempts", "runs"):
        base = project_root.resolve() / rel
        if base.is_dir():
            candidates.update(
                path
                for path in base.rglob("summary.json")
                if _is_contained_file(project_root, path)
            )
    for path in sorted(candidates):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        normalized = {str(key).strip().lower(): value for key, value in payload.items()}
        nested = normalized.get("metrics")
        if isinstance(nested, dict):
            normalized.update(
                {str(key).strip().lower(): value for key, value in nested.items()}
            )
        score_valid = normalized.get("score_valid")
        if score_valid is False or str(score_valid).strip().lower() in {"0", "false", "no"}:
            continue
        values = [
            number
            for field in sorted(_MATH_SYNTH_METRICS)
            if (number := _finite_number(normalized.get(field))) is not None
        ]
        if (
            values
            and all(0.0 <= value <= 1.0 for value in values)
            and all(
                math.isclose(value, values[0], rel_tol=1e-9, abs_tol=1e-12)
                for value in values[1:]
            )
        ):
            return path
    raise EvidenceError(
        "no attempts/**/summary.json or runs/**/summary.json contains a finite "
        f"metric field ({', '.join(sorted(_MATH_SYNTH_METRICS))})"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="argus-metric-evidence")
    parser.add_argument(
        "kind",
        choices=("speedrun", "nanogpt", "speedrun-reference", "kernelbench", "math-synth"),
    )
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()
    validators = {
        "speedrun": validate_speedrun_evidence,
        "nanogpt": validate_nanogpt_evidence,
        "speedrun-reference": validate_speedrun_reference,
        "kernelbench": validate_kernelbench_evidence,
        "math-synth": validate_math_synth_evidence,
    }
    try:
        path = validators[args.kind](root)
    except EvidenceError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"OK: structured metric evidence in {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
