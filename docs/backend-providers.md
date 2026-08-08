# Backend providers

Argus drives five agent CLIs. Two of them — **Pi** and **OpenCode** — are
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
| `pi` | the id verbatim, or `<provider>/<id>` when `ARGUS_SKILL_PI_PROVIDER` is set | Pi resolves a bare id against its authenticated catalogs |
| `opencode` | `<provider>/<id>`, built from `ARGUS_SKILL_OPENCODE_PROVIDER` | `opencode run --model` rejects a bare id, so without the provider the model setting is dropped |

Run the CLI's own listing to see what you actually hold keys for:

```bash
pi --list-models
opencode auth list
```

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
