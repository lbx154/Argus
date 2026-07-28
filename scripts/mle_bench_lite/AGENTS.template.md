# MLE-Bench Lite slot contract

- Competition: `__COMPETITION__`
- Assigned physical GPU: `__GPU__`; the process environment exposes this as
  the only permitted CUDA device. Use logical CUDA device 0 and do not alter
  the visibility variables.
- `data/` is the only benchmark-data path available to the agent.
- Never inspect Kaggle credentials, SSH files, MLE-Bench private/answers/gold
  files, or another competition. Controller-generated aggregate feedback in
  `PRIVATE_LEADERBOARD_FEEDBACK.json` is the only allowed private feedback.
- Every new submission must pass Reviewer approval. Grading is external and may
  occur multiple times for distinct approved submission hashes.
- Keep code and artifacts inside this project root.
- Preserve failed approaches and produce reusable reviewed learning for later
  Lite tasks when warranted.
