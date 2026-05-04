# argus-skill

> **Supervised skill-driven coding agent.**
> A merge of [skill-agent](https://github.com/lbx154/skill-agent)'s
> *horizontal* skill reuse and [ArgusBot](../ArgusBot)'s *vertical*
> reviewer-loop supervision.

## What it is

argus-skill runs a coding task through this pipeline:

```
task → skill matcher → distill new skill if no high-fit match
     ↓
     supervised engineer round-loop
       round k: engineer turn → user-provided checks → reviewer
         done    → write skill back, return success
         continue → inject reviewer.next_action, next round
         blocked  → stop with reason
```

* **Horizontal layer** (from skill-agent): every task seeds a markdown
  *capability skill* in `skills/`. Future similar tasks reuse the same
  skill at near-zero cost. The matcher is fit-graded — only `high`-fit
  skills are applied, so the wrong skill cannot drag the engineer into a
  sibling sub-domain.

* **Vertical layer** (from ArgusBot): the engineer doesn't ship as a
  one-shot. A reviewer sub-agent reads the engineer's output plus the
  user's acceptance checks (`pytest -q`, `make test`, etc.) and emits a
  structured `{done, continue, blocked}` verdict. On `continue` the
  reviewer's `next_action` is fed back into the next engineer round.

These two layers are orthogonal and multiplicative: the matcher cuts the
search-space cost; the reviewer-loop cuts the failure cost.

## Quick demo (no LLM required)

The repo ships an in-memory deterministic backend that scripts canned
responses, so you can smoke-test the loop end-to-end without an API
key:

```bash
pip install -e .
ARGUS_SKILL_BACKEND=memory argus-skill run "say hi"
```

Output (a JSON-shaped LoopOutcome):

```json
{
  "status": "done",
  "rounds": 1,
  "skill_used": "Demo capability",
  "skill_distilled": true,
  "reason": "Task met the demo criterion.",
  "final_message": "Done: read the task and replied as instructed. ..."
}
```

Run `argus-skill list-skills` to see the skill the demo distilled.

## Real usage (with codex / claude-code)

There are two ways to drive a real CLI:

### Option A — `ARGUS_SKILL_BACKEND=codex` (zero code)

The CLI ships a `CodexRunnerBackend` adapter that wraps ArgusBot's
battle-tested `codex_autoloop.codex_runner.CodexRunner`. Set the env
vars and run:

```bash
ARGUS_SKILL_BACKEND=codex \
ARGUS_SKILL_RUNNER_BACKEND=codex \
ARGUS_SKILL_RUNNER_BIN=$(which codex) \
argus-skill run "fix the failing tests in src/foo/" --check 'pytest -q'
```

Honoured env vars:

| Variable | Meaning | Default |
|----------|---------|---------|
| `ARGUS_SKILL_BACKEND` | `memory` (stub) or `codex` (real CLI) | `memory` |
| `ARGUS_SKILL_RUNNER_BACKEND` | which CLI: `codex` / `claude` / `copilot` | `codex` |
| `ARGUS_SKILL_RUNNER_BIN` | path to the CLI binary | resolved on `$PATH` |
| `ARGUS_SKILL_RUNNER_EXTRA_ARGS` | shell-quoted args appended to every call | empty |

Requires ArgusBot to be importable (`pip install -e ../ArgusBot`).

### Option B — custom backend (full control)

For non-codex / non-claude CLIs, import `SkillLoop` from your own
driver script and pass a `RunnerBackend` that wraps your CLI:

```python
from pathlib import Path
from argus_skill import SkillLoop, SkillLoopConfig
from argus_skill.core.ports import RunnerBackend
from argus_skill.core.models import RunnerOptions, RunnerResult

class CodexBackend:
    """Wrap subprocess `codex exec` here. Implement run_exec(...)."""
    def run_exec(self, *, prompt, options: RunnerOptions, run_label,
                 resume_thread_id=None) -> RunnerResult:
        ...  # call your CLI, parse stdout, return RunnerResult

backend = CodexBackend()
loop = SkillLoop(
    skills_dir=Path("skills"),
    scientist_runner=backend,
    engineer_runner=backend,
    reviewer_runner=backend,   # can be a cheaper model
    config=SkillLoopConfig(
        scientist_model="gpt-5.4",
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4-mini",
        max_rounds=3,
        check_commands=["pytest -q"],
    ),
)

outcome = loop.run("fix the failing tests in src/foo/")
print(outcome.status, outcome.round_count, outcome.skill_used)
```

The same `RunnerBackend` interface works for codex, claude-code, copilot
CLI, or anything else — only the wrapper changes.

## Run as a 7×24 daemon (with optional Telegram control)

argus-skill ships a foreground daemon that keeps a single `SkillLoop`
alive across many tasks, accepts commands from a JSONL bus and/or a
Telegram bot, and writes a status JSON for external monitoring.

```bash
# 1) Start the daemon (foreground; pin in a tmux/systemd unit).
argus-skill daemon \
    --state-dir ./argus-state \
    --skills-dir ./skills \
    --max-rounds 3 \
    --telegram-bot-token "$TELEGRAM_BOT_TOKEN" \
    --telegram-chat-id   "$TELEGRAM_CHAT_ID"
```

Then, from any other shell on the same host:

```bash
# Queue a task.
argus-skill daemon-run "add a healthcheck to src/server.py"  --state-dir ./argus-state

# Inject extra guidance for the next reviewer round.
argus-skill daemon-inject "use pytest, not unittest"          --state-dir ./argus-state

# Inspect health (returns non-zero if status is stale or pid is dead).
argus-skill daemon-status --state-dir ./argus-state

# Graceful shutdown.
argus-skill daemon-stop   --state-dir ./argus-state
```

From Telegram (in the configured chat):

| Telegram message | Effect |
| --- | --- |
| `/run <task>`           | Queue a new task. |
| `/inject <text>` (or plain text) | Append guidance to the next round's prompt. |
| `/skip`                 | Abort the currently-running task (next event boundary). |
| `/status` / `/stat`     | Get the current daemon status. |
| `/stop`                 | Gracefully shut the daemon down. |
| `/help`                 | Show a one-line command summary. |

Implementation notes:

* `--state-dir` holds three artifacts: `inbox.jsonl` (control bus —
  what the daemon reads), `outbox.jsonl` (event log — what the daemon
  emits), and `status.json` (heartbeat + last outcome).
* The Telegram poller uses long-polling on `getUpdates`; no inbound
  webhook server is required.
* A token-lock file in `/tmp/argusbot-token-locks` prevents two daemons
  from fighting over the same Telegram bot. Pass `--no-token-lock` to
  bypass for debug runs.
* Without `--telegram-*` flags the daemon still works — control comes
  exclusively from the JSONL bus / `argus-skill daemon-*` CLI.

## Architecture at a glance

```
argus_skill/
├── core/
│   ├── models.py        # CheckResult, ReviewDecision, RunnerOptions, RunnerResult, LoopOutcome
│   └── ports.py         # RunnerBackend, SkillSource, ControlChannel, EventSink protocols
├── scientist/
│   ├── prompts.py       # vendored from skill-agent (Coverage check + When NOT to use + fit-graded matcher)
│   └── distiller.py     # calls runner with Prompts.distill(...)
├── skills/
│   └── store.py         # vendored from skill-agent (markdown cache + fit-graded matcher)
├── engineer/
│   ├── reviewer.py      # vendored from ArgusBot (refactored to take RunnerBackend protocol)
│   ├── reviewer_schema.json
│   ├── checks.py        # vendored from ArgusBot
│   └── runner.py        # SupervisedEngineer: round-loop control flow (NEW)
├── adapters/
│   ├── memory_backend.py  # deterministic stub for tests / smoke runs (NEW)
│   ├── codex_backend.py   # CodexRunnerBackend — wraps ArgusBot's CodexRunner (NEW)
│   ├── control_channels.py  # LocalBus + Telegram control channels for the daemon (NEW)
│   └── event_sinks.py     # Terminal + JSONL + Telegram event sinks (NEW)
├── daemon/
│   ├── token_lock.py    # vendored verbatim from ArgusBot (single-process token guard)
│   ├── bus.py           # vendored verbatim from ArgusBot (JsonlCommandBus + status helpers)
│   └── runtime.py       # Daemon class: 7×24 wrapper around SkillLoop (NEW)
├── telegram/
│   ├── poller.py        # slim Telegram getUpdates poller + command parser (NEW)
│   └── notifier.py      # slim Telegram sendMessage / sendDocument (NEW)
├── apps/
│   ├── cli.py           # `argus-skill run` / `list-skills` (NEW)
│   └── daemon_app.py    # `argus-skill daemon` / `daemon-status|stop|inject|run` (NEW)
└── loop.py              # SkillLoop — the matcher × distiller × supervised-engineer GLUE (NEW)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a per-file
provenance map ("which file came from which upstream").

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

The default suite runs in <1s and uses only the in-memory backend. The
end-to-end smoke test (`tests/test_loop_smoke.py`) exercises:

* matcher miss → distill → 2 rounds (continue → done) → skill writeback
* second-run cache hit (matcher returns high-fit, no redistill)
* reviewer says blocked → loop short-circuits in 1 round
* every round says continue → loop hits `max_rounds` cleanly
* `--no-distill-on-miss` falls back to running engineer skill-less

## Status

v0.1 alpha. The loop is end-to-end tested with stubbed backends; the
real codex / claude adapters are not yet vendored — they live in the
upstream skill-agent and ArgusBot repos and need a small refactor to
implement the new `RunnerBackend` protocol.

## License

MIT — see [LICENSE](LICENSE).

## Provenance

* [skill-agent](https://github.com/lbx154/skill-agent) (Phase A as of
  2026-05-03): the `skill_store.py`, `prompts.py`, and the
  Coverage / When NOT to use / fit-graded matcher prompt engineering.
* [ArgusBot](../ArgusBot): the `reviewer.py`, `reviewer_schema.json`,
  `checks.py`, and the LoopEngine round-loop control flow.
* New code in argus-skill: `core/models.py`, `core/ports.py`,
  `engineer/runner.py` (SupervisedEngineer), `loop.py` (SkillLoop), the
  in-memory backend, the CLI, the tests.
