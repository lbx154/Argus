---
name: "Engineer Process Audit"
description: "审查执行证据是否支持完成声明。 Inspect task-bound execution receipts and logs when an artifact or claimed verification is disputed; distinguish missing telemetry from an action not performed."
---

# Engineer Process Audit

Use for a concrete discrepancy between a result and its claimed execution.
Do not re-run an entire workflow because a result is unusually good, or treat an
internally consistent summary as independent proof.

## Establish what the record can prove

- Identify the current mission, round, command, working directory and artifact
  revision from the supplied evidence. Do not mix another task's success into it.
- Prefer the supplied execution receipt, evaluator output and agent I/O recording.
  `<life_dir>/events.jsonl` is a project timeline, not a complete command ledger.
- The default `signal` event mode omits ordinary progress/commands. Rotation and
  truncation also limit coverage. **No log entry does not prove no execution.**
- Parse JSON fields rather than matching whitespace in serialized JSON. The writer
  uses compact JSON, so a pattern requiring a space after `:` misses valid events.

For a bounded view of recorded progress, pass the actual supplied log path:

```bash
python - /path/from/context/events.jsonl <<'PYCODE'
import json
import sys
from collections import deque

recent = deque(maxlen=40)
malformed = 0
with open(sys.argv[1], encoding="utf-8") as stream:
    for line in stream:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if event.get("type") == "engineer.progress":
            recent.append(event)
print(json.dumps({"recorded_progress_tail": list(recent),
                  "malformed_records": malformed,
                  "complete_execution_history": False}, ensure_ascii=False))
PYCODE
```

Use the configured interpreter. This preview establishes only what is recorded;
its empty result and exit code are not an execution verdict.

## Compare evidence, then decide

Trace consequential numbers to their producing command, actual configuration and
unchanged evaluator. Check suspected shortcuts in context: mocks can be legitimate
unit tests, while replacing a real benchmark with a constant cannot support its
claim. A frozen scorer is useful only when evidence binds its invocation and
output to the current artifact.

Report **confirmed discrepancy**, **supported execution**, or **insufficient
evidence** through the current Reviewer schema. For insufficient evidence, request
the existing receipt or the cheapest decisive check; do not accuse the Engineer
of fabrication. A verified mismatch warrants the smallest repair and recheck.
Respect current role authority: do not edit the scored work, completion state or
verification definition to make the claim pass. No monitoring loop or extra audit
report is required.
