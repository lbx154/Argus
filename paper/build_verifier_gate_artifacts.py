from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_NAME = "verifier_gate_contrast"
TSV_COLUMNS = [
    "row_id",
    "status",
    "source_bundle",
    "source_row_id",
    "mode",
    "condition",
    "task_id",
    "claim",
    "argus_no_reviewer",
    "argus_benchmark_verifier_gate",
    "zero_touch_success",
    "human_interactions_after_assignment",
    "active_touch_minutes_after_assignment",
    "manual_commands",
    "manual_rescue",
    "intervention_severity",
    "needs_human",
    "result_json",
    "stdout_log",
    "stderr_log",
    "verification_log",
    "evidence_1",
    "evidence_2",
    "evidence_3",
    "notes",
]

CONTRAST_BUNDLE = "benchmarks/evidence/tb2-reviewer-gate-contrast-20260515T201700Z"
MANUAL_BUNDLE = "benchmarks/evidence/tb2-manual-followup-20260515T202500Z"


def _repo_path(path_text: str) -> Path:
    return REPO_ROOT / path_text


def _read_tsv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _validate_source_paths(paths: list[Path]) -> None:
    missing = [str(path.relative_to(REPO_ROOT)) for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError("missing verifier-gate source artifacts: " + ", ".join(missing))


def _load_bundle_rows(bundle_dir: Path) -> dict[str, dict[str, str]]:
    summary = bundle_dir / "summary.tsv"
    if not summary.exists():
        raise FileNotFoundError(f"missing bundle summary: {summary.relative_to(REPO_ROOT)}")
    return {row["job_id"]: row for row in _read_tsv_rows(summary)}


def _build_row(
    *,
    row_id: str,
    status: str,
    source_bundle: str,
    source_row_id: str,
    claim: str,
    notes: str,
    evidence_1: str,
    evidence_2: str,
    evidence_3: str,
    row: dict[str, str],
) -> dict[str, str]:
    return {
        "row_id": row_id,
        "status": status,
        "source_bundle": source_bundle,
        "source_row_id": source_row_id,
        "mode": row.get("mode", ""),
        "condition": row.get("condition", ""),
        "task_id": row.get("task_id", ""),
        "claim": claim,
        "argus_no_reviewer": row.get("argus_no_reviewer", ""),
        "argus_benchmark_verifier_gate": row.get("argus_benchmark_verifier_gate", ""),
        "zero_touch_success": row.get("zero_touch_success", ""),
        "human_interactions_after_assignment": row.get(
            "human_interactions_after_assignment", ""
        ),
        "active_touch_minutes_after_assignment": row.get(
            "active_touch_minutes_after_assignment", ""
        ),
        "manual_commands": row.get("manual_commands", ""),
        "manual_rescue": row.get("manual_rescue", ""),
        "intervention_severity": row.get("intervention_severity", ""),
        "needs_human": row.get("needs_human", ""),
        "result_json": row.get("result_json", ""),
        "stdout_log": row.get("stdout_log", ""),
        "stderr_log": row.get("stderr_log", ""),
        "verification_log": row.get("verification_log", ""),
        "evidence_1": evidence_1,
        "evidence_2": evidence_2,
        "evidence_3": evidence_3,
        "notes": notes,
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


def build_verifier_gate_artifacts(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Path]:
    contrast_dir = _repo_path(CONTRAST_BUNDLE)
    manual_dir = _repo_path(MANUAL_BUNDLE)
    _validate_source_paths(
        [
            contrast_dir / "summary.tsv",
            contrast_dir / "RESULTS.md",
            contrast_dir / "jobs" / "index.tsv",
            contrast_dir / "jobs" / "raw" / "failure-reviewer-off" / "result.json",
            contrast_dir / "jobs" / "raw" / "fix-verifier-gated" / "result.json",
            manual_dir / "summary.tsv",
            manual_dir / "RESULTS.md",
            manual_dir / "logs" / "results.csv",
            manual_dir / "jobs" / "index.tsv",
            manual_dir / "jobs" / "raw" / "manual-followup-annotation" / "result.json",
        ]
    )

    contrast_rows = _load_bundle_rows(contrast_dir)
    manual_rows = _load_bundle_rows(manual_dir)

    failure_row = contrast_rows["failure-reviewer-off"]
    fix_row = contrast_rows["fix-verifier-gated"]
    manual_row = manual_rows["manual-followup-annotation"]

    rows = [
        _build_row(
            row_id="reviewer_off_failure",
            status="historical_only",
            source_bundle=CONTRAST_BUNDLE,
            source_row_id="failure-reviewer-off",
            claim=(
                "Reviewer-off self-satisfaction is the historical contrast: the "
                "source row records the shortcut path without an explicit "
                "benchmark verifier gate."
            ),
            notes=(
                "Historical contrast only; keep this row as non-current evidence and "
                "pair it with the verifier-gated fix row."
            ),
            evidence_1=f"{CONTRAST_BUNDLE}/summary.tsv",
            evidence_2=f"{CONTRAST_BUNDLE}/jobs/raw/failure-reviewer-off/result.json",
            evidence_3=f"{CONTRAST_BUNDLE}/RESULTS.md",
            row=failure_row,
        ),
        _build_row(
            row_id="verifier_gated_repair",
            status="current_evidence",
            source_bundle=CONTRAST_BUNDLE,
            source_row_id="fix-verifier-gated",
            claim=(
                "Verifier-gated repair is the current evidence: the corrected row "
                "records an explicit benchmark verifier gate and zero-touch "
                "completion."
            ),
            notes=(
                "Use this row to show the corrected protocol path rather than the "
                "historical reviewer-off shortcut."
            ),
            evidence_1=f"{CONTRAST_BUNDLE}/summary.tsv",
            evidence_2=f"{CONTRAST_BUNDLE}/jobs/raw/fix-verifier-gated/result.json",
            evidence_3=f"{CONTRAST_BUNDLE}/RESULTS.md",
            row=fix_row,
        ),
        _build_row(
            row_id="manual_followup_annotation",
            status="current_evidence",
            source_bundle=MANUAL_BUNDLE,
            source_row_id="manual-followup-annotation",
            claim=(
                "The manual-follow-up annotation preserves the human-attention "
                "schema and records a failed rescue attempt instead of a success."
            ),
            notes=(
                "This row shows the manual-attention vocabulary used by the paper "
                "and keeps failed rescues distinct from successful ones."
            ),
            evidence_1=f"{MANUAL_BUNDLE}/summary.tsv",
            evidence_2=f"{MANUAL_BUNDLE}/logs/results.csv",
            evidence_3=f"{MANUAL_BUNDLE}/RESULTS.md",
            row=manual_row,
        ),
    ]

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
            "artifact_label": "verifier_gate_contrast",
            "repo_root": str(REPO_ROOT),
            "source_bundles": {
                "reviewer_off_failure": CONTRAST_BUNDLE,
                "verifier_gated_repair": CONTRAST_BUNDLE,
                "manual_followup_annotation": MANUAL_BUNDLE,
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
        help="Directory that will receive the verifier-gate artifacts.",
    )
    args = parser.parse_args(argv)

    outputs = build_verifier_gate_artifacts(args.output_dir)
    for key in ("tsv", "csv", "json"):
        print(outputs[key])
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
