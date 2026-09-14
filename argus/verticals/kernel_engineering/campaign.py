"""Small, practical control loop for kernel optimization campaigns."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

STATUS_PATH = Path("research/KERNEL_CAMPAIGN_STATUS.json")
RESULT_PATH = Path("research/PERFORMANCE_RESULT.json")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _result_rows(path: Path) -> list[dict[str, Any]]:
    data = _read_json(path)
    if isinstance(data, list):
        return data
    for key in ("results", "rows", "benchmarks"):
        rows = data.get(key) if isinstance(data, dict) else None
        if isinstance(rows, list):
            return rows
    raise ValueError(f"{path} contains no benchmark rows")


def compare(
    baseline: Path,
    candidate: Path,
    *,
    metric: str,
    keys: list[str],
    where: dict[str, str],
    lower_is_better: bool,
    correctness_passed: bool,
    min_geomean: float,
    min_row: float,
) -> dict[str, Any]:
    def indexed(path: Path) -> dict[tuple[str, ...], float]:
        rows: dict[tuple[str, ...], float] = {}
        for row in _result_rows(path):
            if any(str(row.get(name)) != value for name, value in where.items()):
                continue
            key = tuple(str(row[name]) for name in keys)
            if key in rows:
                raise ValueError(f"duplicate benchmark row {key}")
            value = float(row[metric])
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid {metric} for row {key}")
            rows[key] = value
        return rows

    base = indexed(baseline)
    cand = indexed(candidate)
    if base.keys() != cand.keys():
        raise ValueError("baseline and candidate benchmark rows do not match")

    rows = []
    for key in sorted(base):
        speedup = base[key] / cand[key] if lower_is_better else cand[key] / base[key]
        rows.append(
            {
                "key": dict(zip(keys, key, strict=True)),
                "baseline": base[key],
                "candidate": cand[key],
                "speedup": speedup,
            }
        )
    speedups = [row["speedup"] for row in rows]
    geomean = math.exp(sum(math.log(value) for value in speedups) / len(speedups))
    worst = min(speedups)
    passed = correctness_passed and geomean >= min_geomean and worst >= min_row
    return {
        "correctness_passed": correctness_passed,
        "metric": metric,
        "keys": keys,
        "baseline": str(baseline),
        "candidate": str(candidate),
        "rows": rows,
        "geomean_speedup": geomean,
        "min_row_speedup": worst,
        "min_geomean_required": min_geomean,
        "min_row_required": min_row,
        "passed": passed,
    }


def _outcomes(project_root: Path) -> list[dict[str, Any]]:
    records = []
    for directory in ("attempts", "experiments"):
        for path in sorted((project_root / directory).glob("*/OUTCOME.json")):
            record = _read_json(path)
            if isinstance(record, dict):
                records.append(record)
    return records


def campaign_status(project_root: Path) -> dict[str, Any]:
    records = _outcomes(project_root)
    winner = next(
        (
            record
            for record in records
            if record.get("execution_status") == "completed"
            and record.get("failure_class") == "none"
            and record.get("idea_status") == "supported"
        ),
        None,
    )
    return {
        "attempt_count": len(records),
        "winner": winner.get("attempt_id") if winner else None,
        "winner_summary": winner.get("summary") if winner else None,
        "stop_optimizing": winner is not None,
        "next_action": "validate_and_deliver" if winner else "continue_optimization",
    }


def write_status(project_root: Path) -> dict[str, Any]:
    status = campaign_status(project_root)
    path = project_root / STATUS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    return status


def performance_passed(project_root: Path) -> bool:
    path = project_root / RESULT_PATH
    return path.is_file() and _read_json(path).get("passed") is True


def planner_task_issues(stage: str, project_root: Path, task: object) -> tuple[str, ...]:
    if stage != "optimize" or not campaign_status(project_root)["stop_optimizing"]:
        return ()
    if not getattr(task, "stage_closing", False):
        return ("a retained winner exists; finish this stage before starting another attempt",)
    if getattr(task, "skip_stage_transition", False):
        return ("the retained-winner task must allow the stage transition",)
    return ()


def stage_completion_issues(stage: str, project_root: Path) -> tuple[str, ...]:
    if stage != "optimize":
        return ()
    status = campaign_status(project_root)
    issues = []
    if not status["stop_optimizing"]:
        issues.append("no supported kernel winner has been retained")
    if not performance_passed(project_root):
        issues.append("paired performance result has not passed")
    return tuple(issues)


def planner_context(project_root: Path) -> str:
    status = write_status(project_root)
    return (
        "## Kernel campaign status\n"
        f"- winner: {status['winner'] or 'none'}\n"
        f"- stop_optimizing: {str(status['stop_optimizing']).lower()}\n"
        f"- next_action: {status['next_action']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--project-root", type=Path, default=Path.cwd())
    check = sub.add_parser("check")
    check.add_argument("--project-root", type=Path, default=Path.cwd())
    compare_cmd = sub.add_parser("compare")
    compare_cmd.add_argument("--project-root", type=Path, default=Path.cwd())
    compare_cmd.add_argument("--baseline", type=Path, required=True)
    compare_cmd.add_argument("--candidate", type=Path, required=True)
    compare_cmd.add_argument("--metric", required=True)
    compare_cmd.add_argument("--key", action="append", required=True)
    compare_cmd.add_argument("--where", action="append", default=[])
    compare_cmd.add_argument("--higher-is-better", action="store_true")
    compare_cmd.add_argument("--correctness-passed", action="store_true")
    compare_cmd.add_argument("--min-geomean", type=float, default=1.01)
    compare_cmd.add_argument("--min-row", type=float, default=0.995)

    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    if args.command == "status":
        print(json.dumps(write_status(root), indent=2))
        return 0
    if args.command == "check":
        issues = stage_completion_issues("optimize", root)
        if issues:
            print("\n".join(issues))
            return 2
        print("kernel campaign ready for validation")
        return 0

    baseline = args.baseline if args.baseline.is_absolute() else root / args.baseline
    candidate = args.candidate if args.candidate.is_absolute() else root / args.candidate
    where = dict(item.split("=", 1) for item in args.where)
    result = compare(
        baseline,
        candidate,
        metric=args.metric,
        keys=args.key,
        where=where,
        lower_is_better=not args.higher_is_better,
        correctness_passed=args.correctness_passed,
        min_geomean=args.min_geomean,
        min_row=args.min_row,
    )
    output = root / RESULT_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
