# MLE-Bench machine policy

- Use only official Kaggle data prepared by MLE-Bench.
- Agent work may read only the mounted competition `prepared/public` view.
- Provenance-recorded public pretrained model weights are allowed when the
  competition rules permit them; competition-specific predictions/labels are not.
- Never access MLE-Bench private answers, gold submissions, Kaggle credentials,
  SSH material, or another competition's data. Only aggregate feedback written
  by the controller to `PRIVATE_LEADERBOARD_FEEDBACK.json` is allowed.
- Each slot uses one assigned B200; never alter GPU visibility.
- New submissions require independent Reviewer approval. External grading may
  occur multiple times for distinct approved hashes, and its aggregate feedback
  may guide later experiments just as in a human competition workflow.
- A task is complete only when `MLE_MEDAL_GATE.json` reports `satisfied: true`;
  Reviewer must keep the project open below the bronze threshold.
- Preserve reproducible code, manifests, failed approaches and reusable
  reviewed learning for later Lite tasks.
