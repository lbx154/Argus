# Harbor Framework integration

Harbor Framework `0.21.x` can invoke Argus itself as an installed agent. One
Harbor trial maps to one bounded Argus project:

```text
Harbor task + sandbox
        |
        v
Argus Manager -> Planner -> Engineer <-> Reviewer
        |
        v
Harbor verifier
```

The adapter does not recreate an Argus-like loop in Harbor. Harbor installs
Argus inside the task environment and calls Argus's normal headless runtime with
the task instruction in a private UTF-8 file. The complete team then works in
the Harbor task workspace until the Planner certifies `project_done` or Harbor's
trial timeout stops the run.

## Requirements

- Python 3.12 or newer on the Harbor host
- Harbor Framework `>=0.21,<0.22`
- Docker or another Harbor environment provider
- an OpenAI model and credentials accepted by Harbor's Codex integration

Install Argus and Harbor in the same host environment:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[harbor]'
```

## Run

```bash
export OPENAI_API_KEY=...

.venv/bin/harbor run \
  --dataset terminal-bench@2.0 \
  --agent argus.integrations.harbor:ArgusHarborAgent \
  --model openai/gpt-5.4-mini \
  --ak reasoning_effort=high
```

Harbor calls `ArgusHarborAgent.run(...)`. The adapter then:

1. installs Codex CLI and Argus in the task environment;
2. configures Harbor's model credentials for Argus's Codex backend;
3. uploads the Harbor instruction as `argus-objective.txt`, without placing the
   objective in process arguments;
4. starts `argus --daemon-fg --continuous --bounded`;
5. treats the one-shot task as stage-closing and requires the native independent
   Reviewer even for low-risk verticals;
6. waits for the complete Argus runtime to finish;
7. reports Argus token/cost totals and completion metadata to Harbor.

The task environment needs Python 3.11+. When its system Python is older, the
installer uses `uv` to provision an isolated Python 3.12 runtime.

## Agent arguments

| Argument | Default | Meaning |
|---|---|---|
| `argus_package` | wheel built from current checkout | optional pip requirement installed instead of the local source |
| `codex_version` | latest | Codex CLI version installed by Harbor |
| `reasoning_effort` | `high` | effort used by all four Argus roles |
| `timeout` | Harbor trial timeout | optional inner Argus timeout in seconds |

By default, a source checkout builds its current wheel on the Harbor host,
uploads it, and installs that exact wheel in the task environment. This means
uncommitted adapter/runtime changes are evaluated too.

When the adapter is loaded from an installed package rather than a source
checkout, point `argus_package` at an immutable wheel or Git revision:

```bash
.venv/bin/harbor run \
  --dataset terminal-bench@2.0 \
  --agent argus.integrations.harbor:ArgusHarborAgent \
  --model openai/gpt-5.4-mini \
  --ak 'argus_package=argus @ https://packages.example/argus.whl'
```

The package reference is shell-quoted before it is passed to pip. Prefer an
immutable version, wheel digest, or commit instead of a moving branch.

## Trial artifacts

Harbor's agent log directory contains:

- `argus-objective.txt` — exact task given to Argus;
- `argus-runtime.log` — headless Argus stdout/stderr;
- `argus-state/` — complete persistent Argus project state, including
  `continuous.json`, `events.jsonl`, backlog, checkpoints, and transcripts;
- `codex-home/sessions/` — native model sessions created by Argus roles.

After the trial, Harbor's `AgentContext.metadata.argus` records whether the
bounded project completed, the Planner's done reason, state path, model call
count, and pricing status. Token and cost fields are populated from Argus's own
usage ledger.

## Compatibility boundary

The initial direct adapter uses Argus's Codex backend because Harbor already has
a maintained Codex installer and model-credential contract. The invoked Argus
runtime is otherwise the normal complete runtime: Manager, Planner, Engineer,
Reviewer, persistent state, checkpoints, and completion gates are not replaced.

The optional dependency is pinned to Harbor `0.21.x`. Test the installed-agent
contract before widening that range.
