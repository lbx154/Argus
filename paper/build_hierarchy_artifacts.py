from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_NAME = "slm_llm_human_hierarchy"
TSV_COLUMNS = [
    "row_id",
    "tier",
    "layer_index",
    "status",
    "source_bundle",
    "claim",
    "evidence_1",
    "evidence_2",
    "evidence_3",
    "notes",
]

SOURCE_ROWS: list[dict[str, str]] = [
    {
        "row_id": "slm",
        "tier": "SLM",
        "layer_index": "1",
        "status": "current_evidence",
        "source_bundle": "experiments/tb2-bare-gpt54-mini-20260515T212131Z",
        "claim": (
            "SLM->LLM->HUMAN tier 1: the small-model baseline is grounded in the "
            "tracked bare-gpt54-mini TB2 run."
        ),
        "evidence_1": "experiments/tb2-bare-gpt54-mini-20260515T212131Z/manifest.json",
        "evidence_2": "experiments/tb2-bare-gpt54-mini-20260515T212131Z/status.json",
        "evidence_3": (
            "experiments/tb2-bare-gpt54-mini-20260515T212131Z/"
            "jobs/2026-05-15__21-21-32/cancel-async-tasks__cJwJjAt/result.json"
        ),
        "notes": (
            "The SLM tier is evidence-preserving but not an archive bundle; use the "
            "tracked run root for provenance and avoid inventing new quantitative "
            "claims."
        ),
    },
    {
        "row_id": "llm",
        "tier": "LLM",
        "layer_index": "2",
        "status": "current_evidence",
        "source_bundle": "benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z",
        "claim": (
            "SLM->LLM->HUMAN tier 2: the larger-model baseline is grounded in the "
            "archived bare-gpt54 TB2 bundle."
        ),
        "evidence_1": "benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z/summary.tsv",
        "evidence_2": "benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z/RESULTS.md",
        "evidence_3": "benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z/jobs/index.tsv",
        "notes": (
            "Measured reward, wall_minutes, infra-failure kind, and missing-cause "
            "annotations are preserved; do not invent new quantitative claims."
        ),
    },
    {
        "row_id": "human",
        "tier": "HUMAN",
        "layer_index": "3",
        "status": "current_evidence",
        "source_bundle": "benchmarks/evidence/tb2-manual-followup-20260515T202500Z",
        "claim": (
            "SLM->LLM->HUMAN tier 3: the human-follow-up layer is grounded in the "
            "manual-followup annotation bundle."
        ),
        "evidence_1": (
            "benchmarks/evidence/tb2-manual-followup-20260515T202500Z/summary.tsv"
        ),
        "evidence_2": (
            "benchmarks/evidence/tb2-manual-followup-20260515T202500Z/RESULTS.md"
        ),
        "evidence_3": (
            "benchmarks/evidence/tb2-manual-followup-20260515T202500Z/jobs/index.tsv"
        ),
        "notes": (
            "This tier covers zero-touch success, human interactions after "
            "assignment, active touch minutes, manual commands, manual rescue, "
            "and intervention severity."
        ),
    },
]


def _repo_relative(path_text: str) -> Path:
    return REPO_ROOT / path_text


def _validate_source_paths(rows: list[dict[str, str]]) -> None:
    missing = [
        value
        for row in rows
        for key, value in row.items()
        if key.startswith("evidence_") or key == "source_bundle"
        if not _repo_relative(value).exists()
    ]
    if missing:
        raise FileNotFoundError(
            "missing hierarchy source artifacts: " + ", ".join(sorted(set(missing)))
        )


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


def build_hierarchy_artifacts(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Path]:
    rows = [dict(row) for row in SOURCE_ROWS]
    _validate_source_paths(rows)

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
            "artifact_version": 1,
            "hierarchy_label": "SLM->LLM->HUMAN",
            "repo_root": str(REPO_ROOT),
            "source_bundles": {
                row["tier"]: row["source_bundle"] for row in rows
            },
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
        help="Directory that will receive the hierarchy artifacts.",
    )
    args = parser.parse_args(argv)

    outputs = build_hierarchy_artifacts(args.output_dir)
    for key in ("tsv", "csv", "json"):
        print(outputs[key])
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
