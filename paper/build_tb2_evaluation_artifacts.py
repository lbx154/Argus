from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_NAME = "tb2_comparison"

TB2_BUNDLES: list[dict[str, str]] = [
    {
        "row_id": "argus_v12_redux",
        "source_bundle": "benchmarks/evidence/tb2-argus-v12-redux-20260515T201322Z",
    },
    {
        "row_id": "bare_gpt54",
        "source_bundle": "benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z",
    },
    {
        "row_id": "argus_v12_true_023000z",
        "source_bundle": "benchmarks/evidence/tb2-argus-v12-true-20260516T023000Z",
        "notes": (
            "Completed v12-true evidence with reward=0.011236, wall_minutes=6.16, "
            "n_total_trials=89, n_completed_trials=89, n_errored_trials=80, "
            "cost_usd=3.55539, and residual Docker Hub pull-rate failures across "
            "80 trial logs; cite as the newest detached state, not as a clean "
            "success."
        ),
    },
]

TSV_COLUMNS = [
    "row_id",
    "status",
    "source_bundle",
    "source_row_kind",
    "condition",
    "reward",
    "wall_minutes",
    "n_total_trials",
    "n_completed_trials",
    "n_running_trials",
    "n_pending_trials",
    "n_errored_trials",
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
    "evidence_1",
    "evidence_2",
    "evidence_3",
    "notes",
]


def _repo_path(path_text: str) -> Path:
    return REPO_ROOT / path_text


def _read_tsv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _validate_source_paths(paths: list[Path]) -> None:
    missing = [str(path.relative_to(REPO_ROOT)) for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError("missing TB2 source artifacts: " + ", ".join(missing))


def _load_aggregate_row(bundle_dir: Path) -> dict[str, str]:
    summary = bundle_dir / "summary.tsv"
    if not summary.exists():
        raise FileNotFoundError(f"missing bundle summary: {summary.relative_to(REPO_ROOT)}")
    rows = _read_tsv_rows(summary)
    try:
        return next(row for row in rows if row.get("row_kind") == "aggregate")
    except StopIteration as exc:
        raise FileNotFoundError(
            f"missing aggregate row in {summary.relative_to(REPO_ROOT)}"
        ) from exc


def _format_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _build_row(
    *, row_id: str, bundle: str, row: dict[str, str], notes: str | None = None
) -> dict[str, str]:
    return {
        "row_id": row_id,
        "status": "current_evidence",
        "source_bundle": bundle,
        "source_row_kind": _format_field(row.get("row_kind")),
        "condition": _format_field(row.get("condition")),
        "reward": _format_field(row.get("reward")),
        "wall_minutes": _format_field(row.get("wall_minutes")),
        "n_total_trials": _format_field(row.get("n_total_trials")),
        "n_completed_trials": _format_field(row.get("n_completed_trials")),
        "n_running_trials": _format_field(row.get("n_running_trials")),
        "n_pending_trials": _format_field(row.get("n_pending_trials")),
        "n_errored_trials": _format_field(row.get("n_errored_trials")),
        "exception_kind": _format_field(row.get("exception_kind")),
        "exception_count": _format_field(row.get("exception_count")),
        "infra_failure_kind": _format_field(row.get("infra_failure_kind")),
        "infra_failure_count": _format_field(row.get("infra_failure_count")),
        "input_tokens": _format_field(row.get("input_tokens")),
        "cached_input_tokens": _format_field(row.get("cached_input_tokens")),
        "output_tokens": _format_field(row.get("output_tokens")),
        "cost_usd": _format_field(row.get("cost_usd")),
        "input_tokens_missing_cause": _format_field(row.get("input_tokens_missing_cause")),
        "cached_input_tokens_missing_cause": _format_field(
            row.get("cached_input_tokens_missing_cause")
        ),
        "output_tokens_missing_cause": _format_field(row.get("output_tokens_missing_cause")),
        "cost_usd_missing_cause": _format_field(row.get("cost_usd_missing_cause")),
        "evidence_1": f"{bundle}/summary.tsv",
        "evidence_2": f"{bundle}/RESULTS.md",
        "evidence_3": f"{bundle}/jobs/index.tsv",
        "notes": notes
        or (
            "Comparison row backed by the archived TB2 bundle; keep the generated "
            "table and bundle-local paths together."
        ),
    }


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    _write_text(
        path,
        "\n".join(
            [
                "\t".join(TSV_COLUMNS),
                *[
                    "\t".join(row.get(column, "") for column in TSV_COLUMNS)
                    for row in rows
                ],
                "",
            ]
        ),
    )


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def build_tb2_evaluation_artifacts(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Path]:
    rows: list[dict[str, str]] = []
    bundles: dict[str, str] = {}
    for bundle_info in TB2_BUNDLES:
        bundle = bundle_info["source_bundle"]
        bundle_dir = _repo_path(bundle)
        _validate_source_paths(
            [
                bundle_dir / "summary.tsv",
                bundle_dir / "RESULTS.md",
                bundle_dir / "jobs" / "index.tsv",
            ]
        )
        aggregate = _load_aggregate_row(bundle_dir)
        rows.append(
            _build_row(
                row_id=bundle_info["row_id"],
                bundle=bundle,
                row=aggregate,
            )
        )
        bundles[bundle_info["row_id"]] = bundle

    output_dir.mkdir(parents=True, exist_ok=True)
    tsv_path = output_dir / f"{ARTIFACT_NAME}.tsv"
    csv_path = output_dir / f"{ARTIFACT_NAME}.csv"
    json_path = output_dir / f"{ARTIFACT_NAME}.json"

    _write_tsv(tsv_path, rows)
    _write_csv(csv_path, rows)
    _write_json(
        json_path,
        {
            "artifact_name": ARTIFACT_NAME,
            "artifact_label": ARTIFACT_NAME,
            "artifact_version": 1,
            "repo_root": str(REPO_ROOT),
            "source_bundles": bundles,
            "rows": rows,
        },
    )
    return {"tsv": tsv_path, "csv": csv_path, "json": json_path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory that will receive the TB2 comparison artifacts.",
    )
    args = parser.parse_args(argv)

    outputs = build_tb2_evaluation_artifacts(args.output_dir)
    for key in ("tsv", "csv", "json"):
        print(outputs[key])
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
