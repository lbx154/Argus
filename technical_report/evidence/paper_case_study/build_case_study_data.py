#!/usr/bin/env python3
"""Convert private trajectory aggregates into public case-study summaries."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path


META = {
    "bench-fragile-leaderboard": ("Fragile leaderboard", "Evaluation reliability"),
    "cv-compositional-match": ("Compositional match", "Vision--language matching"),
    "cv-frontier": ("CV frontier", "Test-time adaptation"),
    "mm-gui-agent": ("GUI agent", "Multimodal agents"),
    "mm-hallucination": ("MM hallucination", "Multimodal hallucination"),
    "quant-vocab-matrix": ("Vocabulary matrix", "Model quantization"),
}


def parse_time(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    papers: list[dict[str, object]] = []
    transitions: list[dict[str, object]] = []

    for raw in source:
        project = str(raw["project"])
        short_label, domain = META[project]
        stage_counts: dict[str, int] = {}
        for item in raw.get("stage_history") or []:
            direction = str(item.get("direction") or "unknown")
            stage_counts[direction] = stage_counts.get(direction, 0) + 1

        paper = {
            "project": project,
            "short_label": short_label,
            "title": raw["title"],
            "domain": domain,
            "venue_format": raw["final_format"],
            "elapsed_hours": raw["elapsed_hours"],
            "missions": raw["missions"],
            "missions_done": raw["missions_done"],
            "missions_failed": raw["missions_failed"],
            "engineer_rounds": raw["rounds"],
            "review_continue": (raw.get("review_verdicts") or {}).get("continue", 0),
            "review_done": (raw.get("review_verdicts") or {}).get("done", 0),
            "review_blocked": (raw.get("review_verdicts") or {}).get("blocked", 0),
            "session_rolls": raw["session_rolls"],
            "stage_advances": stage_counts.get("advance", 0),
            "stage_rollbacks": stage_counts.get("rollback", 0),
            "stage_completions": stage_counts.get("complete", 0),
            "recorded_cost_usd": raw["recorded_cost_usd"],
            "academic_review_snapshots": raw["academic_review_iterations"],
            "layout_review_snapshots": raw["layout_review_iterations"],
            "infrastructure_review_snapshots": raw["infrastructure_review_iterations"],
            "academic_score": raw["academic_score"],
            "layout_score": raw["layout_score"],
            "infrastructure_score": raw["infrastructure_score"],
            "pipeline_complete": raw["pipeline_complete"],
            "submission_assurance": raw["submission_assurance_verdict"],
            "final_pages": raw["final_pages"],
            "final_figures": raw["final_figures"],
            "final_tables": raw["final_tables"],
            "reviewer_questions": raw["reviewer_questions"],
        }
        papers.append(paper)

        start = parse_time(str(raw["campaign_start_utc"]))
        end = parse_time(str(raw["campaign_end_utc"]))
        span = max(end - start, 1.0)
        for sequence, item in enumerate(raw.get("stage_history") or [], start=1):
            at = parse_time(str(item["at"]))
            transitions.append(
                {
                    "project": project,
                    "short_label": short_label,
                    "sequence": sequence,
                    "elapsed_hours": round((at - start) / 3600.0, 4),
                    "normalized_progress": round((at - start) / span, 6),
                    "direction": item.get("direction"),
                    "from_stage": item.get("from_stage"),
                    "to_stage": item.get("to_stage"),
                }
            )

    aggregate = {
        "papers": len(papers),
        "pipeline_complete": sum(bool(row["pipeline_complete"]) for row in papers),
        "aggregate_campaign_hours": round(sum(float(row["elapsed_hours"]) for row in papers), 2),
        "missions": sum(int(row["missions"]) for row in papers),
        "engineer_rounds": sum(int(row["engineer_rounds"]) for row in papers),
        "review_continue": sum(int(row["review_continue"]) for row in papers),
        "review_done": sum(int(row["review_done"]) for row in papers),
        "review_blocked": sum(int(row["review_blocked"]) for row in papers),
        "session_rolls": sum(int(row["session_rolls"]) for row in papers),
        "stage_rollbacks": sum(int(row["stage_rollbacks"]) for row in papers),
        "review_snapshots": sum(
            int(row["academic_review_snapshots"])
            + int(row["layout_review_snapshots"])
            + int(row["infrastructure_review_snapshots"])
            for row in papers
        ),
        "recorded_cost_usd": round(sum(float(row["recorded_cost_usd"]) for row in papers), 2),
        "submission_assurance_pass": sum(row["submission_assurance"] == "PASS" for row in papers),
    }

    payload = {
        "schema": "argus-autonomous-paper-case-study/v1",
        "aggregate": aggregate,
        "papers": papers,
        "notes": [
            "Campaign hours are summed across projects and are not calendar time because projects overlap.",
            "Reviewer verdicts and session rolls come from structured Argus events.",
            "Stage transitions come from the canonical pipeline-state history.",
            "Recorded cost sums priced mission-completion and Planner events; unpriced utility calls are excluded.",
            "One compositional-matching assurance snapshot remained blocked on Manager stage authority although the canonical pipeline and final PDF were complete.",
        ],
    }
    (args.out_dir / "paper_trajectory_summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    paper_fields = list(papers[0].keys())
    with (args.out_dir / "paper_trajectory_summary.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=paper_fields)
        writer.writeheader()
        writer.writerows(papers)

    transition_fields = list(transitions[0].keys())
    with (args.out_dir / "stage_transitions.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=transition_fields)
        writer.writeheader()
        writer.writerows(transitions)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
