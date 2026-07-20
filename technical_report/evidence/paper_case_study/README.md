# Autonomous Paper-Production Case Study

This directory contains the public aggregates used by the technical report's
six-paper case study.

The source projects were produced by the Argus research vertical and contain
structured event streams, canonical pipeline-state histories, review histories,
submission-assurance records, and final paper PDFs. The public bundle retains only
paper-facing aggregates, scientific findings, and sanitized Stage transitions;
prompts, private model reasoning, absolute paths, session identifiers, and raw
event streams are excluded.

## Files

- `paper_trajectory_summary.json` contains aggregate and per-paper metrics.
- `paper_trajectory_summary.csv` is a tabular export for independent analysis.
- `stage_transitions.csv` contains normalized Manager-controlled Stage transitions.
- `paper_scientific_findings.json` records the paper-facing question and principal
  result extracted from each final manuscript.
- `mm_hallucination_trace.json` contains the sanitized events used by the
  representative paper-production trajectory.
- `build_case_study_data.py` converts a private aggregate export into these public
  files while dropping project-local details.

Campaign hours are summed across projects and are not calendar time because several
projects overlap. Recorded cost includes priced mission-completion and Planner
events; utility calls without a priced event are excluded.
