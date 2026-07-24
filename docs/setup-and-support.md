# Setup and support contract

## Canonical installation

The public beta path is npm:

```bash
npm install -g @argusevolve/argus@beta
argus --setup
argus
```

A source checkout is the development fallback:

```bash
git clone https://github.com/lbx154/argus-skill.git
cd argus-skill
python -m venv .venv
. .venv/bin/activate
pip install -e .
argus --setup
```

Both paths use the same setup, doctor, cockpit, and daemon commands.

## Platform and runtime support

| Surface | Supported | Notes |
|---|---|---|
| npm beta | Linux x64, Windows x64 | Native binary packages; Node.js 22+ is required for the Copilot CLI |
| Source fallback | Linux, macOS, Windows | Python 3.11-3.13 |
| Interactive UI | `argus` | Human cockpit |
| Supervised automation | `argus --daemon-fg` | Foreground worker for systemd, process managers, and debugging |
| Persistent operation | `argus --daemon` | Detached unattended worker |

## Backend and authentication support

| Backend | Install | Authentication | Version policy |
|---|---|---|---|
| GitHub Copilot CLI | `npm install -g @github/copilot` | `copilot login` | Current stable CLI |
| OpenAI Codex CLI | `npm install -g @openai/codex@latest` | `codex login` or explicit model-API mode | Stable `>=0.128.0`; tested recommendation `0.144.5` |
| Claude Code | `npm install -g @anthropic-ai/claude-code` | `claude auth login` | Current stable CLI |
| OpenCode | `curl -fsSL https://opencode.ai/install \| bash` | `opencode auth login` | Current stable CLI |

Codex prereleases are rejected unless the operator explicitly passes
`--allow-prerelease` or sets `ARGUS_SKILL_ALLOW_BACKEND_PRERELEASE=1`.
The tested recommendation is not an exact pin.

Argus persists an explicit backend/auth profile only after readiness succeeds:

- `subscription_cli`: use the selected CLI's existing authentication. Codex in
  this mode does not require unrelated model-API vault routes.
- `model_api`: Codex only. Required routes must exist in the capability vault
  and pass the setup probe.

The vault defaults to
`~/.argus-skill/capabilities/model_api.json`. Override it with
`ARGUS_SKILL_CAPABILITY_VAULT`; `--life-dir` selects project/session state and
does not select the vault.

## Safe and noninteractive setup

Interactive setup explains user-level changes and defaults to leaving global
Git identity and backend-owned authentication files unchanged. Opt in with
`--set-git-global` or `--configure-codex`.

For automation:

```bash
argus --setup --non-interactive \
  --backend codex \
  --auth-mode subscription_cli \
  --accept-house-rules
```

Noninteractive setup never changes global Git identity or backend auth files.
Its stable exits are:

| Exit | Meaning |
|---:|---|
| 0 | Ready and persisted |
| 2 | Invalid/missing setup policy input |
| 3 | Backend, version, authentication, or capability not ready |
| 4 | Validated configuration could not be persisted |

`argus --doctor --backend <name>` runs the same backend/version/auth readiness
contract. Startup repeats a lightweight read-only check before Manager calls or
campaign mutation so drift fails visibly and leaves the campaign unarmed.
