# Backend providers

Argus drives eight agent CLIs. Two of them — **Pi** and **OpenCode** — are
provider-agnostic fronts: the CLI holds credentials for one or more provider
catalogs, and which one serves a request depends on the model id you select.
This page covers how Argus picks that catalog, and what changed in the
`fix/backend-provider-generalization` release.

## How Argus selects a model

Argus passes the model id you configured (`ARGUS_SKILL_MODEL`, or a per-role
`ARGUS_SKILL_<ROLE>_MODEL`) to the backend CLI:

| Backend | What Argus sends | Notes |
|---|---|---|
| `codex` | the id verbatim | single catalog |
| `copilot` | the id verbatim | single catalog |
| `claude` | the id verbatim | single catalog |
| `grok` | the id verbatim | xAI Grok Build catalog; login with `grok login` or set `XAI_API_KEY` |
| `qoder` | the id verbatim | Qoder CLI (`qodercli`, a Claude Code fork); `qodercli login` or set `QODER_PERSONAL_ACCESS_TOKEN` |
| `dsh` | `provider/model` through the env-driven overlay (`ARGUS_DSH_PROVIDER` / `ARGUS_DSH_MODEL`), bare id selects the overlay's provider | DeepSeek Harness headless runner; authenticates with `DEEPSEEK_API_KEY` |
| `pi` | the id verbatim, or `<provider>/<id>` when `ARGUS_SKILL_PI_PROVIDER` is set | Pi resolves a bare id against its authenticated catalogs |
| `opencode` | `<provider>/<id>`, built from `ARGUS_SKILL_OPENCODE_PROVIDER` | `opencode run --model` rejects a bare id, so without the provider the model setting is dropped |

Run the CLI's own listing to see what you actually hold keys for:

```bash
pi --list-models
opencode auth list
grok --version
qodercli --list-models
```

## Grok Build

Install and authenticate xAI's official CLI:

```bash
curl -fsSL https://x.ai/cli/install.sh | bash
grok login
argus --setup --non-interactive --backend grok
```

For CI or another headless host, set `XAI_API_KEY` instead of starting the
browser login flow. Argus invokes Grok with its native
`streaming-messages-json` protocol, passes role prompts through a private
temporary `--prompt-file`, records the returned session ID, and resumes later
turns with `--resume`.

Read-only roles receive only `read_file`, `grep`, and `list_dir`. Trusted
unattended execution maps Argus full-auto mode to Grok's `--yolo`; project or
organization deny rules still take precedence inside Grok.

## Qoder

Install Qoder's official CLI and authenticate:

```bash
npm install -g @qoder-ai/qodercli
qodercli login            # browser OAuth; tokens refresh automatically
argus --setup --non-interactive --backend qoder
```

For CI or a headless daemon, create a Personal Access Token at
`https://qoder.com/account/integrations` and set `QODER_PERSONAL_ACCESS_TOKEN`
instead of the browser login. `qodercli` stores config under `~/.qoder`; point
`QODER_CONFIG_DIR` at durable storage when `$HOME` is ephemeral.

`qodercli` is a Claude Code fork, so Argus reuses the entire `claude` code path:
it invokes `qodercli -p --output-format stream-json`, passes the model with
`--model`, and resumes sessions with `--resume`. List the models your account
holds with `qodercli --list-models`, then set `ARGUS_SKILL_ENGINEER_MODEL` (and
the other per-role model knobs) to one of them.

## DSH (DeepSeek Harness)

Install the launcher and put a DeepSeek API key in the launching environment:

```bash
npm install -g @deepseek-ai/dsh
export DEEPSEEK_API_KEY=sk-...     # or set it on the dsh web Models page
argus --setup --non-interactive --backend dsh
```

dsh has no stream-json surface, no session resume, and no model flag: Argus
boots `dsh --profile headless "<task>"`, which runs one full agent turn with
the Code Mode tool set, prints only the final assistant text, and exits 0 on
completion. The per-role model and access policy are injected through an
env-driven overlay attached via `--patch`:

* `ARGUS_SKILL_<ROLE>_MODEL` maps to `ARGUS_DSH_MODEL` (a `provider/model`
  value also sets `ARGUS_DSH_PROVIDER`; a bare id keeps the overlay's
  `deepseek-official` provider). A selection stored in the dsh settings
  (the web Models page) still wins over the overlay.
* Read-only roles run under dsh's `read-only` sandbox preset
  (`DSH_PERMISSION_MODE=read-only`); every other role runs with approvals
  disabled (`danger-full-access`), because a headless boot has no approver
  to answer an "ask" prompt. Override the approval policy with
  `ARGUS_DSH_APPROVAL`.
* Each round starts a fresh dsh session (there is no resume), so round
  context travels in the prompt. Argus disables its byte-level idle watchdog
  for dsh — the headless runner is silent until the final message, and
  caller-side thresholds (e.g. the SELF-reply path's 5s/120s) assume a
  streaming CLI — the only knob that re-enables the stages is the
  operator's explicit `ARGUS_SKILL_RUNNER_*_IDLE_SECONDS`; hung turns are
  bounded by dsh's own internal request/tool timeouts instead.

Oversized role prompts (above 90 KiB) are written into the role's working
directory as a mission file the agent reads, instead of the argv positional.

## Setting a provider

```bash
export ARGUS_SKILL_PI_PROVIDER=deepseek
export ARGUS_SKILL_OPENCODE_PROVIDER=deepseek
```

Both are also editable from the cockpit `/config` view, which persists them
across restarts.

For **Pi** the setting is optional. Set it only when two authenticated catalogs
carry the same model id — `claude-opus-5`, for example, exists on both
`anthropic` and a Copilot proxy — and you want to choose deliberately rather
than let Pi pick.

For **OpenCode** it is effectively required whenever you configure a model:
without it Argus cannot build a selector OpenCode accepts, so it drops the
`--model` flag and OpenCode runs its own default. Argus logs a warning when
this happens.

## What `argus --doctor` checks

For Grok, readiness checks the CLI version and verifies that either
`XAI_API_KEY` or a cached `grok login` credential exists without spending a
model turn. Grok does not currently expose a read-only auth-status command, so
an expired cached login is reported when the first provider call asks for
reauthentication.

For Qoder, readiness treats a set `QODER_PERSONAL_ACCESS_TOKEN` as ready;
otherwise it runs `qodercli --list-models`, which exits non-zero until you log
in, so an unauthenticated CLI is reported without spending a model turn.

For DSH, readiness treats an exported `DEEPSEEK_API_KEY` as ready. dsh exposes
no read-only auth-status command that does not cost a model call, so an
unauthenticated setup is reported directly instead of probed.

For the Pi backend, readiness reads `pi --list-models` — which lists only
AUTHENTICATED models — and reports:

- **failure** when `ARGUS_SKILL_PI_PROVIDER` names a provider you hold no key
  for. This is deterministic: every call would fail with
  `No API key found for <provider>`.
- **warning** when a configured role model is not listed for the effective
  provider, or is listed by more than one provider. These are warnings rather
  than failures because `pi --model` also accepts fuzzy patterns, so an id
  missing from the table can still resolve.

## Breaking change: Pi no longer assumes GitHub Copilot

**Before.** Argus prefixed every bare Pi model id with `github-copilot/`. The
default was invisible: it appeared in no README, no `--setup` prompt, and no
cockpit setting.

**Effect.** Any Pi authenticated against a different provider — a native
DeepSeek key, Anthropic, Azure, a local vLLM — failed on *every* call with
`No API key found for github-copilot`, while `pi --list-models` (and therefore
`argus --doctor`) still reported the backend healthy.

**After.** A bare id is passed through untouched and Pi resolves it against its
own authenticated catalogs. `ARGUS_SKILL_PI_PROVIDER` still exists, now purely
as a disambiguator.

**If you run Pi in front of a Copilot proxy**, set the provider explicitly once:

```bash
export ARGUS_SKILL_PI_PROVIDER=github-copilot   # or whatever `pi --list-models` calls it
```

Confirm the name with `pi --list-models` before setting it — a Pi configured
through `models.json` often names the proxy something else (`copilot-forward`
is common), in which case the old hardcoded default was already wrong.

## Related: the cheap control-plane routes

Four routes deliberately use a smaller model than the role's own: Manager
front-door classification, bounded-DAG decomposition, `/plan` preview, and
interactive prompt rewrite. They pick an OpenAI id only on backends that serve
the OpenAI catalog (`codex`, `copilot`); on every other backend they fall back
to the role's configured model.

`pi` used to be counted in that OpenAI group, so these four routes asked for
`gpt-5.4-mini` regardless of how the operator had configured Argus. Override
any of them individually if you want a cheaper model on your own provider:

| Route | Knob |
|---|---|
| Manager front-door classify | `ARGUS_SKILL_FRONTDOOR_MODEL` |
| Bounded-DAG decomposition | `ARGUS_SKILL_BOUNDED_DAG_MODEL` |
| `/plan` preview | `ARGUS_SKILL_PLAN_PREVIEW_MODEL` |
| Prompt rewrite | `ARGUS_SKILL_REWRITE_MODEL` |
