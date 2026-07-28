# MLE-Bench Lite slot contract

- Competition: `__COMPETITION__`
- Assigned physical GPU: `__GPU__`; the process environment exposes this as
  the only permitted CUDA device. Use logical CUDA device 0 and do not alter
  the visibility variables.
- `data/` is the only benchmark-data path available to the agent.
- Public pretrained representation weights are allowed with recorded provenance
  when competition rules permit; imported competition predictions/labels are not.
- A read-only shared ML dependency layer is exposed through `PYTHONPATH` and
  `ARGUS_MLE_PYDEPS` when available. Test imports before installing anything.
  Do not copy PyTorch, CUDA, torchvision, NumPy, SciPy, Pillow, or scikit-learn
  into the project when the shared layer already provides them. Reviewer should
  reject an unexplained project-local dependency tree larger than 1 GiB.
- Keep downloaded public model weights under `solution/artifacts/pretrained/`
  with URL, version, and checksum provenance; do not mix weights with packages.
- Never inspect Kaggle credentials, SSH files, MLE-Bench private/answers/gold
  files, or another competition. Controller-generated aggregate feedback in
  `PRIVATE_LEADERBOARD_FEEDBACK.json` is the only allowed private feedback.
- Every new submission must pass Reviewer approval. Grading is external and may
  occur multiple times for distinct approved submission hashes.
- Reviewer must not certify project completion until `MLE_MEDAL_GATE.json`
  exists with `satisfied: true` (bronze medal or better).
- Keep code and artifacts inside this project root.
- Preserve failed approaches and produce reusable reviewed learning for later
  Lite tasks when warranted.
