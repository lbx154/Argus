---
name: argus-digest
description: "One screen on a project from Argus's record: state, cost, time, parallel work, each task, the operator's letters, the latest team letters, letters no one needed"
---

Run it on a project's record folder (the one named under "Where things are"), or on the folder of
projects to read the most recently active one:

```bash
python3 <this folder>/digest.py <record folder>                 # the whole picture
python3 <this folder>/digest.py <record folder> --task t3       # one task
python3 <this folder>/digest.py <record folder> --grep 'vargo'  # search commands, thoughts and letters
python3 <this folder>/digest.py <record folder> --json          # the numbers alone
```

Reach for it instead of reading `events.jsonl` by hand: to tell the operator where the work stands
and what it has cost, to see whether a task is stuck (who holds it, for how long, how often it went
back), or, as the chief, to look across projects. It only reads. The review and send-back counts
come from the letters' wording: a guide, not a record Argus keeps.
