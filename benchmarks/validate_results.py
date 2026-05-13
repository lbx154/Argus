"""Validate checked-in benchmark result directories against the protocol."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

REQUIRED_FILES = {
    "PLAN.md",
    "BUILD_INFO.md",
    "aggregate.py",
    "RESULTS.md",
    "summary.tsv",
}

REQUIRED_SUMMARY_COLUMNS = {
    "cond",
    "task",
    "reward",
    "wall_s",
    "eng_in_tok",
    "eng_cached_in_tok",
    "eng_out_tok",
    "rev_in_tok",
    "rev_cached_in_tok",
    "rev_out_tok",
    "sci_tokens",
    "sci_cached_in_tok",
    "model_eng",
    "model_rev",
    "model_sci",
    "cost_usd",
}


@dataclass(frozen=True)
class ValidationIssue:
    path: Path
    message: str


def iter_experiment_dirs(results_root: Path) -> list[Path]:
    """Return all top-level result directories."""
    return [child for child in sorted(results_root.iterdir()) if child.is_dir()]


def validate_experiment_dir(exp_dir: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if (exp_dir / "EXEMPT.md").exists():
        return issues

    for name in sorted(REQUIRED_FILES):
        path = exp_dir / name
        if not path.exists():
            issues.append(ValidationIssue(path=exp_dir, message=f"missing required file: {name}"))

    jobs_dirs = [p for p in exp_dir.rglob("jobs") if p.is_dir()]
    if not jobs_dirs:
        issues.append(ValidationIssue(path=exp_dir, message="missing required jobs/ transcript directory"))

    run_scripts = list(exp_dir.glob("run-*.sh"))
    if not run_scripts:
        issues.append(ValidationIssue(path=exp_dir, message="missing required run-*.sh script"))

    log_files = [p for p in exp_dir.glob("*.log") if p.is_file()]
    if not log_files:
        issues.append(ValidationIssue(path=exp_dir, message="missing required .log transcript"))

    summary_path = exp_dir / "summary.tsv"
    if summary_path.exists():
        issues.extend(validate_summary(summary_path))

    return issues


def validate_summary(summary_path: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    try:
        with summary_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh, delimiter="\t")
            header = next(reader)
    except StopIteration:
        return [ValidationIssue(path=summary_path, message="summary.tsv is empty")]
    except OSError as exc:
        return [ValidationIssue(path=summary_path, message=f"unable to read summary.tsv: {exc}")]

    columns = set(header)
    missing = sorted(REQUIRED_SUMMARY_COLUMNS - columns)
    if missing:
        issues.append(
            ValidationIssue(
                path=summary_path,
                message=f"missing required summary columns: {', '.join(missing)}",
            )
        )
    return issues


def validate_results_root(results_root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not results_root.exists():
        return [ValidationIssue(path=results_root, message="results root does not exist")]

    for exp_dir in iter_experiment_dirs(results_root):
        issues.extend(validate_experiment_dir(exp_dir))
    return issues


def _format_issue(issue: ValidationIssue) -> str:
    return f"{issue.path}: {issue.message}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "results_root",
        nargs="?",
        default="benchmarks/results",
        help="Path to the benchmark results root.",
    )
    args = parser.parse_args(argv)

    issues = validate_results_root(Path(args.results_root))
    if issues:
        for issue in issues:
            print(_format_issue(issue))
        return 1
    print(f"Validated {args.results_root}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
