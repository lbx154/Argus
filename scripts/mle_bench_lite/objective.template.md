Run one bounded, fair MLE-Bench Lite competition: __COMPETITION__.

Use only the mounted `data/` directory, which is the official MLE-Bench
`prepared/public` view. Never search for or access prepared/private, answers,
gold submissions, Kaggle credentials, SSH material, or another competition's
workspace. The controller may expose aggregate feedback from Reviewer-approved
submissions in `PRIVATE_LEADERBOARD_FEEDBACK.json`; that file is allowed, but
the underlying private labels and grader implementation remain forbidden.

Goal: produce the strongest reproducible `submission.csv` possible within 24
hours on the single assigned B200 GPU __GPU__. Use train-derived validation and
the competition's exact metric. Search public papers, Kaggle discussions and
public code when useful, but record source provenance and never import
predictions or labels.

Required deliverables in this project root:

- `submission.csv` in the exact sample-submission schema and row order;
- `solution/` containing executable training/inference code;
- `RUN_MANIFEST.json` with metric, folds/seeds, environment, commands and hashes;
- `RESULTS.md` with validation results, failed approaches and resource use;
- `CHECKPOINT.md` maintained as current state.

The independent Reviewer must verify schema, public-data-only access,
reproducibility and claim strength. Do not run `mlebench grade-sample` or submit
to Kaggle yourself. After each Reviewer-approved new submission hash, the
external controller grades it and returns aggregate private-leaderboard feedback.
Use that feedback like a human competitor, but every later submission must again
pass independent Reviewer approval.

This run is part of a fixed-model self-evolution sequence. Reuse relevant shared
Skills/Wiki knowledge from earlier competitions. When this task yields a
genuinely reusable MLE procedure or failure diagnosis, maintain it in the
project Skill/Wiki layer so reviewed learning can propagate to later tasks.
