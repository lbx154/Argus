# SWE-Bench Pro report data

This directory contains the report-facing summary of one continuous 731-task
SWE-Bench Pro evaluation.

## Headline comparison

- Direct Copilot uses GPT-5.5 with xhigh reasoning and reaches approximately 59%
  accuracy.
- Argus uses the same GPT-5.5/xhigh solve backbone, continuously updates its Skill
  and Wiki state, and reaches approximately 78% accuracy.
- Aggregate Argus token use is approximately 1.41x the Direct Copilot total.
- Copilot per-wave token and time records are not available. The longitudinal plots
  therefore show Argus only and must not imply a per-wave Copilot comparison.

These aggregate values are frozen in `unified_experiment_summary.json` as the
author-confirmed experiment summary.

## Argus wave analysis

`argus_wave_efficiency.csv` contains the 22 completed comparable Wave summaries
used for the convergence plot. It keeps only the fields needed by the paper:

- completed tasks;
- final-valid-run solve input tokens per task;
- active Argus workflow seconds per task;
- cumulative Skill and Wiki counts.

The primary metrics exclude worker waiting, image pulls, workspace copying,
container setup, verifier execution, infrastructure retries, and explicit post-task
Skill maintenance. Waves 13 and 15 were deferred. Waves 23 and 24 are retained but
displayed separately as hard-tail stress rather than hidden inside the mature
window.

The report figure is generated as editable PowerPoint with:

```bash
python figures/build_swebench_evolution_pptx.py
```

The script also exports vector PDF/SVG, a PNG preview, LaTeX macros, the grouped
window CSV, and a provenance manifest.

## Reviewer mechanism analysis

- `reviewer_mechanism_stats.json` freezes the Reviewer/self-review routing split
  and intervention funnel.
- `reviewer_interventions.csv` lists all 43 external-Reviewer trajectories that
  contain a revision request.
- The strict rescue count requires `Reviewer continue -> Engineer revision ->
  Reviewer done`; the broader recovery count uses the official verifier outcome.

The editable mechanism figure is regenerated with:

```bash
python figures/build_reviewer_mechanism_pptx.py
```
