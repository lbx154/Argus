from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_NAME = "user_study_metrics"

PROMPT_ONLY_BUNDLE = "benchmarks/evidence/prompt-only-tb2-smoke-20260515T1435Z"
MANUAL_BUNDLE = "benchmarks/evidence/tb2-manual-followup-20260515T202500Z"

TSV_COLUMNS = [
    "row_id",
    "source_kind",
    "status",
    "source_bundle",
    "source_row_id",
    "condition",
    "rows",
    "accepted_rate",
    "mean_reward",
    "needs_human_rate",
    "zero_touch_success_rate",
    "known_zero_touch_success",
    "human_interactions_after_assignment_total",
    "known_human_interactions_after_assignment",
    "active_touch_minutes_after_assignment_total",
    "known_active_touch_minutes_after_assignment",
    "manual_commands_total",
    "rescue_rate",
    "manual_rescue_counts",
    "intervention_severity_counts",
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


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_source_paths(paths: list[Path]) -> None:
    missing = [str(path.relative_to(REPO_ROOT)) for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError("missing user-study source artifacts: " + ", ".join(missing))


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


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _counter_json(rows: list[dict[str, str]], key: str) -> str:
    counts = Counter(_format_value(row.get(key)) or "none" for row in rows)
    return json.dumps(dict(sorted(counts.items())), sort_keys=True)


def _prompt_only_condition_row(
    *,
    bundle: str,
    condition: str,
    summary_json: dict[str, Any],
    summary_rows: list[dict[str, str]],
) -> dict[str, str]:
    condition_payload = summary_json["conditions"][condition]
    cond_rows = [row for row in summary_rows if _format_value(row.get("condition")) == condition]
    manual_rescue_counts = _counter_json(cond_rows, "manual_rescue")
    intervention_counts = _counter_json(cond_rows, "intervention_severity")
    accepted_rate = condition_payload.get("accepted_rate")
    mean_reward = condition_payload.get("mean_reward")
    return {
        "row_id": f"prompt_only_{condition}",
        "source_kind": "prompt_only_condition",
        "status": "current_evidence",
        "source_bundle": bundle,
        "source_row_id": condition,
        "condition": condition,
        "rows": _format_value(condition_payload.get("rows")),
        "accepted_rate": _format_value(accepted_rate),
        "mean_reward": _format_value(mean_reward),
        "needs_human_rate": _format_value(condition_payload.get("needs_human_rate")),
        "zero_touch_success_rate": _format_value(
            condition_payload.get("zero_touch_success_rate")
        ),
        "known_zero_touch_success": _format_value(
            condition_payload.get("known_zero_touch_success")
        ),
        "human_interactions_after_assignment_total": _format_value(
            sum(int(float(_format_value(row.get("human_interactions_after_assignment")) or "0")) for row in cond_rows)
        ),
        "known_human_interactions_after_assignment": _format_value(
            condition_payload.get("known_human_interactions_after_assignment")
        ),
        "active_touch_minutes_after_assignment_total": _format_value(
            sum(float(_format_value(row.get("active_touch_minutes_after_assignment")) or "0") for row in cond_rows)
        ),
        "known_active_touch_minutes_after_assignment": _format_value(
            condition_payload.get("known_active_touch_minutes_after_assignment")
        ),
        "manual_commands_total": _format_value(
            sum(int(float(_format_value(row.get("manual_commands")) or "0")) for row in cond_rows)
        ),
        "rescue_rate": _format_value(
            condition_payload.get("rescue_rate")
        ),
        "manual_rescue_counts": manual_rescue_counts,
        "intervention_severity_counts": intervention_counts,
        "evidence_1": f"{bundle}/summary.tsv",
        "evidence_2": f"{bundle}/logs/summary.json",
        "evidence_3": f"{bundle}/logs/results.csv",
        "notes": (
            "Condition-level prompt-only metrics derived from the archived smoke "
            "bundle. Keep the generated package and the raw bundle together."
        ),
    }


def _manual_followup_row(bundle: str) -> dict[str, str]:
    bundle_dir = _repo_path(bundle)
    rows = _read_tsv_rows(bundle_dir / "summary.tsv")
    manual_row = next(row for row in rows if row.get("job_id") == "manual-followup-annotation")
    rescue_counts = _counter_json([manual_row], "manual_rescue")
    severity_counts = _counter_json([manual_row], "intervention_severity")
    accepted = _format_value(manual_row.get("needs_human")) != "True"
    return {
        "row_id": "manual_followup_annotation",
        "source_kind": "manual_followup_row",
        "status": "current_evidence",
        "source_bundle": bundle,
        "source_row_id": "manual-followup-annotation",
        "condition": _format_value(manual_row.get("condition")),
        "rows": "1",
        "accepted_rate": "0" if accepted is False else "1",
        "mean_reward": _format_value(manual_row.get("reward")),
        "needs_human_rate": "1",
        "zero_touch_success_rate": "0",
        "known_zero_touch_success": "1",
        "human_interactions_after_assignment_total": _format_value(
            manual_row.get("human_interactions_after_assignment")
        ),
        "known_human_interactions_after_assignment": "1",
        "active_touch_minutes_after_assignment_total": _format_value(
            manual_row.get("active_touch_minutes_after_assignment")
        ),
        "known_active_touch_minutes_after_assignment": "1",
        "manual_commands_total": _format_value(manual_row.get("manual_commands")),
        "rescue_rate": "0",
        "manual_rescue_counts": rescue_counts,
        "intervention_severity_counts": severity_counts,
        "evidence_1": f"{bundle}/summary.tsv",
        "evidence_2": f"{bundle}/logs/results.csv",
        "evidence_3": f"{bundle}/RESULTS.md",
        "notes": (
            "The manual-follow-up annotation preserves the user-study schema and "
            "records a failed rescue attempt instead of a success."
        ),
    }


def build_user_study_artifacts(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Path]:
    prompt_bundle = _repo_path(PROMPT_ONLY_BUNDLE)
    manual_bundle = _repo_path(MANUAL_BUNDLE)
    _validate_source_paths(
        [
            prompt_bundle / "summary.tsv",
            prompt_bundle / "logs" / "summary.json",
            prompt_bundle / "logs" / "results.csv",
            prompt_bundle / "RESULTS.md",
            prompt_bundle / "jobs" / "index.tsv",
            manual_bundle / "summary.tsv",
            manual_bundle / "logs" / "results.csv",
            manual_bundle / "RESULTS.md",
            manual_bundle / "jobs" / "index.tsv",
        ]
    )

    prompt_summary_json = _read_json(prompt_bundle / "logs" / "summary.json")
    prompt_summary_rows = _read_tsv_rows(prompt_bundle / "summary.tsv")
    rows = [
        _prompt_only_condition_row(
            bundle=PROMPT_ONLY_BUNDLE,
            condition="argus",
            summary_json=prompt_summary_json,
            summary_rows=prompt_summary_rows,
        ),
        _prompt_only_condition_row(
            bundle=PROMPT_ONLY_BUNDLE,
            condition="codex",
            summary_json=prompt_summary_json,
            summary_rows=prompt_summary_rows,
        ),
        _manual_followup_row(MANUAL_BUNDLE),
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
            "artifact_label": ARTIFACT_NAME,
            "artifact_version": 1,
            "repo_root": str(REPO_ROOT),
            "source_bundles": {
                "prompt_only_argus": PROMPT_ONLY_BUNDLE,
                "prompt_only_codex": PROMPT_ONLY_BUNDLE,
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
        help="Directory that will receive the user-study artifacts.",
    )
    args = parser.parse_args(argv)

    outputs = build_user_study_artifacts(args.output_dir)
    for key in ("tsv", "csv", "json"):
        print(outputs[key])
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
