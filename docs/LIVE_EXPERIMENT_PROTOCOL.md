# Live Experiment Protocol

Any experiment script written by argus-skill must be observable, interruptible,
and safe to supervise while the agent continues other work.

## Required files

Every run directory must contain these files before the first expensive call:

| File | Purpose |
|---|---|
| `manifest.json` | immutable run description: objective, command, expected trials, model routes, budget, source snapshot |
| `status.json` | latest state: `pending/running/completed/failed/cancelled`, counters, current item, timestamps |
| `progress.jsonl` | append-only event stream, one JSON object per lifecycle event |
| `stdout.log` | full stdout from the experiment worker |
| `stderr.log` | full stderr from the experiment worker |
| `pid` | worker PID when launched in background |
| `STOP` | optional operator-created cancellation file |

For result-producing experiments, also stream partial results to
`run_results.jsonl` (or the run-local equivalent) as soon as each trial finishes.

## Progress event schema

Append and flush one line per meaningful event:

```json
{
  "ts": "2026-05-23T12:00:00Z",
  "event": "trial_done",
  "run_id": "checklist-pilot-20260523T120000Z",
  "done": 17,
  "total": 48,
  "phase": "model_call",
  "protocol": "checklist_verify",
  "task_id": "json_schema_004",
  "status": "passed",
  "message": "validator passed"
}
```

Required event names:

- `run_started`
- `trial_started`
- `trial_done`
- `trial_failed`
- `heartbeat`
- `early_stop`
- `run_completed`
- `run_failed`
- `run_cancelled`

## Flush rule

After every progress event and result row:

1. write a complete JSON line;
2. `flush()`;
3. `os.fsync(file.fileno())` for long-running experiments;
4. update `status.json` atomically via temp file + `os.replace`.

This is what lets terminal progress bars show `17/48` instead of waiting until
the whole experiment exits.

## Non-blocking launch rule

If an experiment may take more than roughly 60 seconds or more than 5 model/API
calls, do not run it as a blocking foreground command inside the agent round.
Instead:

```bash
python experiments/<run_id>/runner.py \
  > experiments/<run_id>/stdout.log \
  2> experiments/<run_id>/stderr.log &
echo $! > experiments/<run_id>/pid
```

Then the agent should:

1. immediately verify the PID is alive;
2. read the first few `progress.jsonl` events;
3. report how to monitor and cancel;
4. continue with independent work such as paper outline, citation TODOs, or
   analysis scripts while the experiment runs.

## Cancellation protocol

Operators can cancel by creating:

```bash
touch experiments/<run_id>/STOP
```

The experiment worker must check for `STOP` before every expensive call and at
least once every 30 seconds. On cancellation it must:

1. stop launching new trials;
2. let the current atomic write finish;
3. write `run_cancelled` to `progress.jsonl`;
4. set `status.json.status = "cancelled"`;
5. exit 130.

Agents may also cancel themselves when invariant checks fail.

## Self-supervision early-stop rules

Experiment workers must support early termination when the run no longer matches
the intended study. Examples:

- validation schema is wrong for 3 consecutive tasks;
- API calls return auth/model errors repeatedly;
- the actual model is not the configured model;
- direct/checklist conditions are not receiving paired task ids;
- a metric is constant or impossible because the validator is broken;
- cost or call count exceeds the manifest budget;
- observed artifacts contradict the stated hypothesis in a way that makes the
  remaining calls wasteful.

When this happens, write an `early_stop` event with `reason`, update
`status.json`, and stop the run rather than silently spending the full budget.

## Agent behavior while waiting

The agent must not sit idle if an experiment is running. It should do one of:

- monitor `progress.jsonl` and summarize changes;
- prepare `paper/main.tex`, figure scripts, or claims tables that do not need
  final numbers yet;
- answer operator questions;
- accept `/notify` or Telegram nudges and incorporate them before the next
  experiment batch;
- cancel or revise the run if the operator points out a design flaw.

## Terminal progress contract

Every demo or run script should expose a simple progress view based on the files
above. Minimum display:

```text
[12:04:15] 35% status=running trials=17/48 current=checklist_verify/json_schema_004 results=17 rows
```

The progress view must read only files, not private process memory, so users can
watch it from another terminal.

