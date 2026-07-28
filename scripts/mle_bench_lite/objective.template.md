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

Provenance-recorded public pretrained model weights (for example ImageNet
checkpoints from torchvision/timm) are allowed when the competition rules permit
them. They are general representations, not competition predictions. Imported
competition labels, predictions, gold submissions, or private feedback beyond
the controller summary remain forbidden.

The sandbox may expose a read-only shared ML dependency layer through
`PYTHONPATH` and `ARGUS_MLE_PYDEPS`. Test imports before installing packages and
reuse that layer for heavy frameworks. Do not vendor duplicate PyTorch, CUDA,
torchvision, NumPy, SciPy, Pillow, or scikit-learn copies when they are already
available. Reviewer must reject unexplained project-local dependency trees over
1 GiB. Store public pretrained weight files separately under
`solution/artifacts/pretrained/` and record their provenance and checksums.

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

Minimum completion contract: this project is not complete until the latest
controller-generated `MLE_MEDAL_GATE.json` has `satisfied: true`, meaning a
bronze medal or better. Reviewer may approve a candidate for grading before this
gate is met, but must reject final project completion while the gate is absent
or false.

This run is part of a fixed-model self-evolution sequence. Reuse relevant shared
Skills/Wiki knowledge from earlier competitions. When this task yields a
genuinely reusable MLE procedure or failure diagnosis, maintain it in the
project Skill/Wiki layer so reviewed learning can propagate to later tasks.
