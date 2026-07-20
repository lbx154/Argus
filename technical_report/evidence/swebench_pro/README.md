# SWE-Bench Pro Reproducibility Data

This directory contains the report-facing summary of one continuous 731-task
SWE-Bench Pro evaluation.

## Headline comparison

- Direct Copilot uses GPT-5.5 with xhigh reasoning and reaches approximately 59%
  accuracy.
- Argus uses the same GPT-5.5/xhigh solve backbone, continuously updates its Skill
  and Wiki state, and reaches approximately 78% accuracy.
- Aggregate Argus Token use is approximately 1.41x the Direct Copilot total.
- Copilot per-Wave Token and time records are unavailable; longitudinal plots
  therefore show Argus only.

The aggregate values are recorded in `unified_experiment_summary.json`.

## Longitudinal analysis

`argus_wave_efficiency.csv` contains the 22 completed, comparable Wave summaries
used by the convergence plot. The reported fields are completed tasks, accepted-run
solve input Tokens per task, active Argus workflow seconds per task, and cumulative
Skill/Wiki counts.

Active workflow time excludes orchestration wait, environment preparation,
external verification, infrastructure recovery, and post-task knowledge
maintenance. Two incomplete Waves are omitted from grouped means. The final
difficult-task Waves are displayed separately from the mature operating window.

Regenerate the editable PowerPoint and vector exports with:

```bash
python figures/build_swebench_evolution_pptx.py
```

## Reviewer analysis

- `reviewer_mechanism_stats.json` records the Reviewer/self-review routing split
  and intervention funnel.
- `reviewer_interventions.csv` contains the 43 Reviewer trajectories with a
  revision request.
- Strict rescue requires `Reviewer continue -> Engineer revision -> Reviewer done`;
  the broader recovery count uses the official verifier outcome.

Regenerate the Reviewer figure with:

```bash
python figures/build_reviewer_mechanism_pptx.py
```
