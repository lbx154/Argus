"""Anti-mediocrity hard gate (F3).

Replaces prompt-level "anti-mediocrity" checklist judgments with deterministic
Python validators that look at real evidence and refuse to advance a project
when the bar is not met.

Three gates:

1. **baseline_reproduced** — at least one named baseline condition must have
   a successful (reward > 0) trial in evidence. A method that only beats
   "random" or "no-op" is not a contribution.

2. **improvement_threshold** — the proposed condition must beat the strongest
   baseline by at least ``min_delta`` aggregate reward (default 0.02). A
   <2% improvement is within trial-level noise and not publishable.

3. **benchmark_diversity** — evidence must cover at least ``min_families``
   distinct benchmark families (default 3). Single-benchmark results
   over-fit the chosen task distribution.

Each validator returns a :class:`MediocrityIssue` list. When called as a CLI,
exits non-zero on any issue and emits a structured JSON report.

CLI:
    python -m argus_skill.skills.anti_mediocrity \\
        --project-root . \\
        --proposed-condition argus-v12-redux \\
        --baseline-condition bare-gpt54 \\
        [--min-delta 0.02] [--min-benchmark-families 3]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

DEFAULT_EVIDENCE_ROOT = Path("benchmarks/evidence")
DEFAULT_MIN_DELTA = 0.02
DEFAULT_MIN_FAMILIES = 3


@dataclass
class MediocrityIssue:
    code: str
    detail: str
    measured: float | int | str | None = None
    threshold: float | int | str | None = None


@dataclass
class AggregateRow:
    """A parsed aggregate row from a bundle's summary.tsv."""

    bundle: str
    condition: str
    reward: float | None
    n_total_trials: int | None
    n_completed_trials: int | None
    n_errored_trials: int | None

    @property
    def is_clean_enough(self) -> bool:
        """Heuristic: fewer than 25% errored trials → clean enough to compare."""
        if not self.n_total_trials or self.n_total_trials <= 0:
            return False
        errored = self.n_errored_trials or 0
        return errored / self.n_total_trials < 0.25


@dataclass
class MediocrityReport:
    project_root: Path
    proposed_condition: str | None
    baseline_condition: str | None
    min_delta: float
    min_families: int
    aggregates: list[AggregateRow] = field(default_factory=list)
    issues: list[MediocrityIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict:
        return {
            "project_root": str(self.project_root),
            "proposed_condition": self.proposed_condition,
            "baseline_condition": self.baseline_condition,
            "min_delta": self.min_delta,
            "min_families": self.min_families,
            "aggregates": [
                {
                    "bundle": a.bundle,
                    "condition": a.condition,
                    "reward": a.reward,
                    "n_total_trials": a.n_total_trials,
                    "n_completed_trials": a.n_completed_trials,
                    "n_errored_trials": a.n_errored_trials,
                    "is_clean_enough": a.is_clean_enough,
                }
                for a in self.aggregates
            ],
            "ok": self.ok,
            "issue_count": len(self.issues),
            "issues": [
                {
                    "code": i.code,
                    "detail": i.detail,
                    "measured": i.measured,
                    "threshold": i.threshold,
                }
                for i in self.issues
            ],
        }


def _coerce_float(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        # Fallback for "89.0"-style fields.
        f = _coerce_float(value)
        return int(f) if f is not None else None


def _load_aggregate_rows(project_root: Path, evidence_root: Path) -> list[AggregateRow]:
    """Scan ``benchmarks/evidence/*/summary.tsv`` for aggregate rows."""
    rows: list[AggregateRow] = []
    abs_root = (project_root / evidence_root).resolve()
    if not abs_root.exists():
        return rows
    for bundle_dir in sorted(abs_root.iterdir()):
        if not bundle_dir.is_dir():
            continue
        summary = bundle_dir / "summary.tsv"
        if not summary.exists():
            continue
        try:
            with summary.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    if (row.get("row_kind") or "").strip() != "aggregate":
                        continue
                    rows.append(
                        AggregateRow(
                            bundle=str(bundle_dir.relative_to(project_root)),
                            condition=(row.get("condition") or "").strip(),
                            reward=_coerce_float(row.get("reward") or ""),
                            n_total_trials=_coerce_int(row.get("n_total_trials") or ""),
                            n_completed_trials=_coerce_int(
                                row.get("n_completed_trials") or ""
                            ),
                            n_errored_trials=_coerce_int(
                                row.get("n_errored_trials") or ""
                            ),
                        )
                    )
        except (OSError, csv.Error):
            continue
    return rows


def _benchmark_families_in_bundles(
    project_root: Path, evidence_root: Path
) -> set[str]:
    """Collect distinct ``dataset_id`` values from manifest.json / metadata."""
    families: set[str] = set()
    abs_root = (project_root / evidence_root).resolve()
    if not abs_root.exists():
        return families
    candidate_names = ("manifest.json", "metadata.json", "metadata.tsv")
    for bundle_dir in abs_root.iterdir():
        if not bundle_dir.is_dir():
            continue
        # JSON manifests / metadata
        for name in candidate_names:
            path = bundle_dir / name
            if not path.exists():
                continue
            try:
                if name.endswith(".json"):
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    ds = _extract_dataset_id(payload)
                    if ds:
                        families.add(ds)
                elif name.endswith(".tsv"):
                    with path.open("r", encoding="utf-8", newline="") as fh:
                        reader = csv.DictReader(fh, delimiter="\t")
                        for row in reader:
                            ds = (row.get("dataset_id") or "").strip()
                            if ds:
                                families.add(ds)
            except (OSError, json.JSONDecodeError, csv.Error):
                continue
    return families


def _extract_dataset_id(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    ds = payload.get("dataset_id")
    if isinstance(ds, str) and ds.strip():
        return ds.strip()
    # nested under "metadata"
    md = payload.get("metadata")
    if isinstance(md, dict):
        ds = md.get("dataset_id")
        if isinstance(ds, str) and ds.strip():
            return ds.strip()
    return None


def check_baseline_reproduced(
    aggregates: list[AggregateRow], baseline_condition: str
) -> list[MediocrityIssue]:
    matches = [
        a
        for a in aggregates
        if a.condition == baseline_condition and a.is_clean_enough
    ]
    if not matches:
        return [
            MediocrityIssue(
                code="baseline_not_reproduced",
                detail=(
                    f"no clean aggregate evidence for baseline condition "
                    f"{baseline_condition!r} (need a bundle with "
                    f"row_kind=aggregate, condition={baseline_condition!r}, "
                    f"and <25% errored trials)"
                ),
            )
        ]
    if not any((a.reward or 0.0) > 0.0 for a in matches):
        return [
            MediocrityIssue(
                code="baseline_zero_reward",
                detail=(
                    f"baseline {baseline_condition!r} has clean evidence but "
                    f"every aggregate reward is 0; that is a failed "
                    f"reproduction, not a baseline. Re-run the baseline."
                ),
                measured=max((a.reward or 0.0) for a in matches),
                threshold=0.0,
            )
        ]
    return []


def check_improvement_threshold(
    aggregates: list[AggregateRow],
    proposed_condition: str,
    baseline_condition: str,
    *,
    min_delta: float = DEFAULT_MIN_DELTA,
) -> list[MediocrityIssue]:
    proposed = [
        a
        for a in aggregates
        if a.condition == proposed_condition and a.is_clean_enough
    ]
    baselines = [
        a
        for a in aggregates
        if a.condition == baseline_condition and a.is_clean_enough
    ]
    if not proposed:
        return [
            MediocrityIssue(
                code="proposed_missing",
                detail=(
                    f"no clean aggregate evidence for proposed condition "
                    f"{proposed_condition!r}"
                ),
            )
        ]
    if not baselines:
        # baseline_not_reproduced will fire separately; don't double-report.
        return []
    best_proposed = max((a.reward or 0.0) for a in proposed)
    best_baseline = max((a.reward or 0.0) for a in baselines)
    delta = best_proposed - best_baseline
    if delta < min_delta:
        return [
            MediocrityIssue(
                code="improvement_below_threshold",
                detail=(
                    f"proposed {proposed_condition!r} best reward "
                    f"{best_proposed:.4f} vs baseline "
                    f"{baseline_condition!r} best {best_baseline:.4f} = "
                    f"Δ{delta:+.4f}, below min_delta {min_delta:.4f}"
                ),
                measured=round(delta, 6),
                threshold=min_delta,
            )
        ]
    return []


def check_benchmark_diversity(
    families: Iterable[str], *, min_families: int = DEFAULT_MIN_FAMILIES
) -> list[MediocrityIssue]:
    families = set(families)
    if len(families) < min_families:
        return [
            MediocrityIssue(
                code="benchmark_diversity_insufficient",
                detail=(
                    f"only {len(families)} distinct benchmark family/families "
                    f"found across evidence bundles "
                    f"({sorted(families) or '[]'}); need at least "
                    f"{min_families} to claim cross-benchmark validity"
                ),
                measured=len(families),
                threshold=min_families,
            )
        ]
    return []


def run_anti_mediocrity_gate(
    project_root: Path,
    *,
    proposed_condition: str | None,
    baseline_condition: str | None,
    evidence_root: Path = DEFAULT_EVIDENCE_ROOT,
    min_delta: float = DEFAULT_MIN_DELTA,
    min_families: int = DEFAULT_MIN_FAMILIES,
) -> MediocrityReport:
    report = MediocrityReport(
        project_root=project_root.resolve(),
        proposed_condition=proposed_condition,
        baseline_condition=baseline_condition,
        min_delta=min_delta,
        min_families=min_families,
    )
    report.aggregates = _load_aggregate_rows(project_root, evidence_root)

    if baseline_condition:
        report.issues.extend(
            check_baseline_reproduced(report.aggregates, baseline_condition)
        )
    if proposed_condition and baseline_condition:
        report.issues.extend(
            check_improvement_threshold(
                report.aggregates,
                proposed_condition,
                baseline_condition,
                min_delta=min_delta,
            )
        )
    families = _benchmark_families_in_bundles(project_root, evidence_root)
    report.issues.extend(
        check_benchmark_diversity(families, min_families=min_families)
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument(
        "--proposed-condition",
        type=str,
        default=None,
        help="Condition name (matches summary.tsv aggregate 'condition').",
    )
    parser.add_argument(
        "--baseline-condition",
        type=str,
        default=None,
        help="Baseline condition name to compare against.",
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=DEFAULT_EVIDENCE_ROOT,
        help="Relative path to evidence root.",
    )
    parser.add_argument(
        "--min-delta",
        type=float,
        default=DEFAULT_MIN_DELTA,
        help="Minimum reward improvement vs baseline (default 0.02).",
    )
    parser.add_argument(
        "--min-benchmark-families",
        type=int,
        default=DEFAULT_MIN_FAMILIES,
        help="Minimum distinct benchmark families (default 3).",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = run_anti_mediocrity_gate(
        args.project_root,
        proposed_condition=args.proposed_condition,
        baseline_condition=args.baseline_condition,
        evidence_root=args.evidence_root,
        min_delta=args.min_delta,
        min_families=args.min_benchmark_families,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_text_report(report)

    return 0 if report.ok else 1


def _print_text_report(report: MediocrityReport) -> None:
    print(f"Anti-mediocrity gate: project={report.project_root}")
    if report.proposed_condition or report.baseline_condition:
        print(
            f"  proposed={report.proposed_condition} "
            f"baseline={report.baseline_condition} "
            f"min_delta={report.min_delta} "
            f"min_families={report.min_families}"
        )
    print(f"  aggregates_loaded={len(report.aggregates)}")
    if report.ok:
        print("OK — all anti-mediocrity gates pass.")
        return
    print(f"FAIL — {len(report.issues)} issue(s):")
    for issue in report.issues:
        msg = f"  [{issue.code}] {issue.detail}"
        if issue.measured is not None and issue.threshold is not None:
            msg += f" (measured={issue.measured}, threshold={issue.threshold})"
        print(msg)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
