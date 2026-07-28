# Argus MLE-Bench Lite two-slot campaign

This controller runs the official 22-competition MLE-Bench Low/Lite split with
two concurrent Argus slots. It simulates the SWE-Bench Pro runtime-evolution
shape: tasks are sequential across waves, successful reviewed project Skills
are promoted into one shared Argus runtime root, and later tasks inherit that
state while the base model stays fixed.

The agent sees only one competition's `prepared/public` directory. Grading runs
outside the sandbox against `prepared/private`. A watcher submits only a stable
new `submission.csv` hash after an independent `round.review.completed` event
with `status=done`; rejected work, profile-only rounds, and duplicate hashes do
not consume a submission. Aggregate leaderboard feedback is written back to the
project and delivered through the Argus operator inbox, while labels and private
files remain inaccessible.

The bubblewrap mount contract also hides the whole campaign root before
re-binding the current project. Other project workspaces, submission snapshots,
grade history, and controller state are therefore not merely forbidden by
prompt—they are absent from the agent filesystem.

Completion is medal-gated: `campaign.py` counts a task complete only after its
bounded run finishes and a Reviewer-approved submission earns bronze or better.
Below-bronze tasks retain their artifacts and feedback and are automatically
requeued for another bounded campaign.

The official repository pins legacy `kaggle<1.7`, while this deployment uses a
new-style `access_token`. A minimal local patch changes only MLE-Bench's raw
download call to the authenticated Kaggle CLI 2.x. Official preparation,
checksums, public/private splitting and graders remain unchanged.

Commands:

```bash
cp config.env.example config.env
# Edit paths and optionally point COMPETITION_LIST at an accessible subset.
./auth_check.sh
./prepare_lite.py --workers 2
./grade_watcher.py
./campaign.py --workers 2
./status.sh
```

`lite.txt` is the official 22-task split. If Kaggle rule acceptance blocks some
competitions, create a separate newline-delimited list and set
`COMPETITION_LIST` in `config.env`; do not silently redefine the official split.
