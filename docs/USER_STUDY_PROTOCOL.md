# User Study Protocol

This document defines the annotation rubric for prompt-only evidence bundles.
It applies to both `results.csv`-style exports and checked-in archive bundles
under `benchmarks/evidence/`.

## Required fields

Each row that participates in the user-study export should preserve these
fields when they are known:

- `zero_touch_success`
- `human_interactions_after_assignment`
- `active_touch_minutes_after_assignment`
- `manual_commands`
- `manual_rescue`
- `intervention_severity`
- `needs_human`
- `exit_code`
- `timed_out`

If a field is not measured, leave it blank. If a field is measured and nothing
happened, record a numeric zero. Do not invent a success or failure outcome
when the transcript only supports "unknown".

## Counting rules

- `human_interactions_after_assignment` counts human actions after the initial
  assignment prompt.
- `active_touch_minutes_after_assignment` counts only active human attention,
  not passive waiting.
- `manual_commands` counts explicit shell or CLI commands used by a human to
  inspect or repair the task.
- `zero_touch_success` is `True` only when the task completed without any
  follow-up human intervention after assignment.

## Rescue rubric

- Leave `manual_rescue` blank when there is no explicit rescue annotation.
- Use `manual_rescue=failed` when a human attempted a rescue but the task was
  not recovered.
- Use `manual_rescue=rescued` or another positive success word only when the
  human intervention actually recovered the task.
- The summary layer treats failed rescue strings as non-successes, so they do
  not contribute to `rescue_rate`.

## Intervention severity

Recommended values:

- `zero_touch`
- `needs_human`
- `manual_followup`
- `manual_rescue`
- `model_drift`
- `reviewer_off_shortcut` for historical control-path rows that record the old
  reviewer-off failure mode without implying a manual intervention; these rows
  should still normalize to zero-touch success unless a separate manual action
  is documented.

`manual_followup` covers a human review or annotation pass that adds evidence
but does not claim recovery. `manual_rescue` covers an intervention that
explicitly attempts to rescue the task.

## Archive expectations

An archival bundle should include:

- `PLAN.md`
- `BUILD_INFO.md`
- `manifest.json` or a run script
- `logs/`
- `summary.tsv`
- `RESULTS.md`
- `jobs/index.tsv`

Job indexes should use repo-relative pointers that resolve from the bundle
root. Do not store machine-local absolute paths unless the file is only a
transcript note and not a pointer consumed by validation.
