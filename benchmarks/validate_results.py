"""Validate checked-in benchmark evidence bundles against the archive contract."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from benchmarks.prompt_only_tb2.summarize_runs import _manual_intervention_recorded

REQUIRED_BUNDLE_FILES = {
    "BUILD_INFO.md",
    "PLAN.md",
    "RESULTS.md",
    "summary.tsv",
}

INDEX_PATH_NAMES = ("index.tsv", "index.csv", "index.json")
PATH_LIKE_COLUMNS = {
    "agent_dir",
    "job_log",
    "trial_log",
    "artifact_dir",
    "bundle_dir",
    "metadata_json",
    "prompt_txt",
    "result_json",
    "verifier_dir",
    "verification_ctrf_json",
    "verification_reward_txt",
    "source_dir",
    "stdout_log",
    "stderr_log",
    "verification_log",
}

STUDY_SUMMARY_COLUMNS = {
    "zero_touch_success",
    "human_interactions_after_assignment",
    "active_touch_minutes_after_assignment",
    "manual_commands",
    "manual_rescue",
    "intervention_severity",
}

STUDY_BUNDLE_PREFIXES = (
    "prompt-only-tb2-smoke-",
    "tb2-manual-followup-",
)

TB2_EXPORT_BUNDLE_TYPE = "tb2_fullbench_export"
TB2_EXPORT_REQUIRED_COLUMNS = {
    "row_kind",
    "job_id",
    "condition",
    "bundle_dir",
    "result_json",
    "job_log",
    "trial_log",
    "metadata_json",
    "verification_log",
    "verification_reward_txt",
    "verification_ctrf_json",
    "agent_dir",
    "verifier_dir",
    "reward",
    "wall_minutes",
    "status",
    "exception_kind",
    "exception_count",
    "infra_failure_kind",
    "infra_failure_count",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "cost_usd",
    "input_tokens_missing_cause",
    "cached_input_tokens_missing_cause",
    "output_tokens_missing_cause",
    "cost_usd_missing_cause",
}


@dataclass(frozen=True)
class ValidationIssue:
    path: Path
    message: str


def _bundle_files_present(bundle_dir: Path) -> bool:
    return all((bundle_dir / name).exists() for name in REQUIRED_BUNDLE_FILES)


def _index_path(bundle_dir: Path) -> Path | None:
    jobs_dir = bundle_dir / "jobs"
    for name in INDEX_PATH_NAMES:
        path = jobs_dir / name
        if path.exists():
            return path
    return None


def _read_table_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix not in {".csv", ".tsv"}:
        return []
    delimiter = "\t" if suffix == ".tsv" else ","
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=delimiter))


def _read_json_rows(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        return [payload]
    return []


def _load_manifest(bundle_dir: Path) -> dict[str, Any]:
    path = bundle_dir / "manifest.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _bundle_type(bundle_dir: Path) -> str:
    payload = _load_manifest(bundle_dir)
    value = payload.get("bundle_type")
    return str(value or "")


def _read_index_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() in {".csv", ".tsv"}:
        return _read_table_rows(path)
    if path.suffix.lower() == ".json":
        return _read_json_rows(path)
    return []


def _validate_summary(summary_path: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    try:
        with summary_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh, delimiter="\t")
            header = next(reader)
            try:
                next(reader)
            except StopIteration:
                issues.append(ValidationIssue(path=summary_path, message="summary.tsv has no data rows"))
    except StopIteration:
        issues.append(ValidationIssue(path=summary_path, message="summary.tsv is empty"))
        return issues
    except OSError as exc:
        issues.append(ValidationIssue(path=summary_path, message=f"unable to read summary.tsv: {exc}"))
        return issues

    if not header:
        issues.append(ValidationIssue(path=summary_path, message="summary.tsv header is empty"))
    return issues


def _validate_study_summary(summary_path: Path, bundle_dir: Path) -> list[ValidationIssue]:
    if not bundle_dir.name.startswith(STUDY_BUNDLE_PREFIXES):
        return []

    issues: list[ValidationIssue] = []
    try:
        with summary_path.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
    except OSError as exc:
        return [ValidationIssue(path=summary_path, message=f"unable to read study summary: {exc}")]

    header = set(rows[0].keys()) if rows else set()
    missing = sorted(STUDY_SUMMARY_COLUMNS.difference(header))
    if missing:
        issues.append(
            ValidationIssue(
                path=summary_path,
                message="missing required study columns: " + ", ".join(missing),
            )
        )
        return issues

    for row_index, row in enumerate(rows, start=1):
        for column in sorted(STUDY_SUMMARY_COLUMNS):
            text = str(row.get(column) or "").strip()
            if not text:
                issues.append(
                    ValidationIssue(
                        path=summary_path,
                        message=f"study row {row_index} missing required field: {column}",
                    )
                )
        needs_human = str(row.get("needs_human") or "").strip().lower()
        zero_touch = str(row.get("zero_touch_success") or "").strip().lower()
        if needs_human in {"false", "0", "no", "n"} and zero_touch in {"false", "0", "no", "n"}:
            if not _manual_intervention_recorded(row):
                issues.append(
                    ValidationIssue(
                        path=summary_path,
                        message=(
                            f"study row {row_index} contradicts needs_human=False with zero_touch_success=False"
                        ),
                    )
                )
    return issues


def _validate_tb2_export_summary(summary_path: Path, bundle_dir: Path) -> list[ValidationIssue]:
    if _bundle_type(bundle_dir) != TB2_EXPORT_BUNDLE_TYPE:
        return []

    issues: list[ValidationIssue] = []
    try:
        with summary_path.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
    except OSError as exc:
        return [ValidationIssue(path=summary_path, message=f"unable to read TB2 export summary: {exc}")]

    header = set(rows[0].keys()) if rows else set()
    missing = sorted(TB2_EXPORT_REQUIRED_COLUMNS.difference(header))
    if missing:
        issues.append(
            ValidationIssue(
                path=summary_path,
                message="missing required TB2 export columns: " + ", ".join(missing),
            )
        )
        return issues

    for row_index, row in enumerate(rows, start=1):
        for column in ("reward", "wall_minutes", "exception_kind", "infra_failure_kind", "status"):
            if not str(row.get(column) or "").strip():
                issues.append(
                    ValidationIssue(
                        path=summary_path,
                        message=f"TB2 export row {row_index} missing required field: {column}",
                    )
                )
        for token_column, cause_column in (
            ("input_tokens", "input_tokens_missing_cause"),
            ("cached_input_tokens", "cached_input_tokens_missing_cause"),
            ("output_tokens", "output_tokens_missing_cause"),
            ("cost_usd", "cost_usd_missing_cause"),
        ):
            token_text = str(row.get(token_column) or "").strip()
            cause_text = str(row.get(cause_column) or "").strip()
            if not token_text and not cause_text:
                issues.append(
                    ValidationIssue(
                        path=summary_path,
                        message=(
                            f"TB2 export row {row_index} missing value and missing-cause for {token_column}"
                        ),
                    )
                )
    return issues


def _validate_index_paths(bundle_dir: Path, rows: Iterable[dict[str, Any]]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for row_index, row in enumerate(rows, start=1):
        for column, value in row.items():
            if column not in PATH_LIKE_COLUMNS:
                continue
            path_text = str(value or "").strip()
            if not path_text:
                continue
            candidate = (bundle_dir / path_text).resolve()
            if not candidate.exists():
                issues.append(
                    ValidationIssue(
                        path=bundle_dir,
                        message=f"jobs index row {row_index} references missing path: {path_text}",
                    )
                )
    return issues


def validate_bundle_dir(bundle_dir: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if (bundle_dir / "EXEMPT.md").exists():
        return issues

    for name in sorted(REQUIRED_BUNDLE_FILES):
        path = bundle_dir / name
        if not path.exists():
            issues.append(ValidationIssue(path=bundle_dir, message=f"missing required file: {name}"))

    logs_dir = bundle_dir / "logs"
    if not logs_dir.exists() or not any(p.is_file() for p in logs_dir.rglob("*")):
        issues.append(ValidationIssue(path=bundle_dir, message="missing required logs/ directory"))

    run_manifest = bundle_dir / "manifest.json"
    run_script = next(bundle_dir.glob("run*.sh"), None)
    if not run_manifest.exists() and run_script is None:
        issues.append(
            ValidationIssue(
                path=bundle_dir,
                message="missing required run manifest or run script",
            )
        )

    jobs_dir = bundle_dir / "jobs"
    if not jobs_dir.exists():
        issues.append(ValidationIssue(path=bundle_dir, message="missing required jobs/ directory"))
        return issues

    index_path = _index_path(bundle_dir)
    if index_path is None:
        issues.append(ValidationIssue(path=bundle_dir, message="missing required jobs/index.* file"))
    else:
        rows = _read_index_rows(index_path)
        if not rows:
            issues.append(ValidationIssue(path=index_path, message="jobs index is empty or unreadable"))
        else:
            issues.extend(_validate_index_paths(bundle_dir, rows))

    summary_path = bundle_dir / "summary.tsv"
    if summary_path.exists():
        issues.extend(_validate_summary(summary_path))
        issues.extend(_validate_study_summary(summary_path, bundle_dir))
        issues.extend(_validate_tb2_export_summary(summary_path, bundle_dir))

    return issues


def validate_experiment_dir(bundle_dir: Path) -> list[ValidationIssue]:
    """Backward-compatible alias for bundle validation."""

    return validate_bundle_dir(bundle_dir)


def iter_bundle_dirs(archive_root: Path) -> list[Path]:
    """Return all top-level bundle directories or the root bundle itself."""
    if _bundle_files_present(archive_root):
        return [archive_root]
    return [child for child in sorted(archive_root.iterdir()) if child.is_dir()]


def validate_results_root(archive_root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not archive_root.exists():
        return [ValidationIssue(path=archive_root, message="archive root does not exist")]
    if not archive_root.is_dir():
        return [ValidationIssue(path=archive_root, message="archive root is not a directory")]

    bundle_dirs = iter_bundle_dirs(archive_root)
    if not bundle_dirs:
        return [ValidationIssue(path=archive_root, message="no bundle directories found")]

    for bundle_dir in bundle_dirs:
        issues.extend(validate_bundle_dir(bundle_dir))
    return issues


def _format_issue(issue: ValidationIssue) -> str:
    return f"{issue.path}: {issue.message}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "archive_root",
        nargs="?",
        default="benchmarks/evidence",
        help="Path to the archive root or a single bundle directory.",
    )
    args = parser.parse_args(argv)

    issues = validate_results_root(Path(args.archive_root))
    if issues:
        for issue in issues:
            print(_format_issue(issue))
        return 1
    print(f"Validated {args.archive_root}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
