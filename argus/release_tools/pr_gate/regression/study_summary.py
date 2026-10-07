"""Audit retained report artifacts without rewriting or replaying the original run."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import statistics
from pathlib import Path

if __package__:
    from .runtime import digest, validate_skill_session, write_json
    from .study import DEFAULT_ROOT
    from .validation import validate_report
else:
    from runtime import digest, validate_skill_session, write_json
    from study import DEFAULT_ROOT
    from validation import validate_report


def summarize(root: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    schema = json.loads((root / "runner/report.schema.json").read_text())
    selection = json.loads((root / "selection.json").read_text())["selected"]
    original = json.loads((root / "summary.json").read_text())
    records = []
    for row in selection:
        number = row["pr_number"]
        directory = root / "results" / f"pr-{number}"
        metadata = json.loads((root / "inputs" / f"pr-{number}" / "input.json").read_text())
        runner = json.loads((directory / "runner-result.json").read_text())
        record = {
            "pr_number": number,
            "url": row["url"],
            "original_runner_status": runner["status"],
            "original_runner_error": runner.get("error"),
            "source_immutability_audit": (
                "performed_in_original_run" if runner["status"] == "completed"
                else "not_performed_before_original_report_rejection"
            ),
            "duration_seconds": runner["duration_seconds"],
            "original_report_sha256": digest(directory / "report.json"),
        }
        try:
            validate_skill_session(directory / "copilot.jsonl")
            report = validate_report(directory, schema, metadata)
        except (OSError, ValueError, KeyError) as exc:
            record.update(artifact_audit="failed", error=f"{type(exc).__name__}: {exc}")
        else:
            confirmed = [
                finding for finding in report["findings"]
                if finding["status"] == "CONFIRMED_REGRESSION"
            ]
            record.update(
                artifact_audit="passed",
                declared_verdict=report["verdict"],
                analysis_mode=report["analysis_mode"],
                supported_candidate_findings=confirmed,
                semantic_adjudication="not_performed",
                probe_count=len(report["tests"]),
            )
        records.append(record)
    passed = [record for record in records if record["artifact_audit"] == "passed"]
    started = dt.datetime.fromisoformat(
        json.loads((root / "execution.json").read_text())["started_at"],
    )
    finished = max(
        dt.datetime.fromisoformat(json.loads(
            (root / "results" / f"pr-{row['pr_number']}" / "runner-result.json").read_text(),
        )["completed_at"])
        for row in selection
    )
    summary = {
        "schema": "copilot-pr-regression-posthoc-artifact-audit/v1",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "original_summary_sha256": digest(root / "summary.json"),
        "original_run_summary": original,
        "posthoc_changes": [
            "Map only /work/... evidence paths into each PR's retained artifact root.",
            "Allow UNCERTAIN with supported local findings; do not alter declared verdicts.",
        ],
        "auditor_sha256": digest(Path(__file__)),
        "validator_sha256": digest(Path(__file__).with_name("validation.py")),
        "selected_prs": len(selection),
        "report_artifact_audit_counts": dict(collections.Counter(
            record["artifact_audit"] for record in records
        )),
        "declared_verdict_counts": dict(collections.Counter(
            record["declared_verdict"] for record in passed
        )),
        "analysis_modes": dict(collections.Counter(record["analysis_mode"] for record in passed)),
        "prs_with_supported_candidate_findings": sum(
            bool(record["supported_candidate_findings"]) for record in passed
        ),
        "supported_candidate_finding_count": sum(
            len(record["supported_candidate_findings"]) for record in passed
        ),
        "paired_probe_count": sum(record["probe_count"] for record in passed),
        "wall_minutes": round((finished - started).total_seconds() / 60, 2),
        "median_pr_minutes": round(statistics.median(
            record["duration_seconds"] for record in records
        ) / 60, 2),
        "limitations": [
            "Post-hoc artifact audit only: no models, probes, or source-integrity audits were rerun.",
            "The nine original report rejections skipped the worker-side source-integrity audit.",
            "Source revisions/receipt identities and retained log hashes are checked, not oracle validity.",
            "Model-produced regression candidates are not independently adjudicated bugs.",
            "No-regression-found is scoped; no accuracy or precision/recall is established.",
        ],
    }
    write_json(output / "summary.json", summary)
    write_json(output / "records.json", records)
    lines = [
        "# Historical PR Regression Study: Final Artifact Summary", "",
        "All 50 Copilot sessions finished. This file separates original runner",
        "acceptance from a post-hoc audit of the unchanged reports and receipts.", "",
        f"- Wall time: {summary['wall_minutes']} minutes.",
        f"- Median per PR: {summary['median_pr_minutes']} minutes.",
        f"- Original runner: {original['status_counts']}.",
        f"- Post-hoc artifact audit: {summary['report_artifact_audit_counts']}.",
        f"- Unchanged declared verdicts: {summary['declared_verdict_counts']}.",
        f"- Analysis modes: {summary['analysis_modes']}.",
        f"- Paired probes: {summary['paired_probe_count']}.",
        f"- PRs with supported candidate findings: "
        f"{summary['prs_with_supported_candidate_findings']}.", "",
        "## Candidate findings, not independently adjudicated defects", "",
        "| PR | Declared verdict | Candidate hypotheses |",
        "|---|---|---|",
    ]
    for record in passed:
        if not record["supported_candidate_findings"]:
            continue
        hypotheses = "; ".join(
            finding["hypothesis"].replace("|", "\\|").replace("\n", " ")
            for finding in record["supported_candidate_findings"]
        )
        lines.append(
            f"| [lbx154/Argus#{record['pr_number']}]({record['url']}) "
            f"| {record['declared_verdict']} | {hypotheses} |"
        )
    lines += ["", "## Evidence limits", ""]
    lines += [f"- {limitation}" for limitation in summary["limitations"]]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(summarize(args.root, args.output), indent=2))


if __name__ == "__main__":
    main()
