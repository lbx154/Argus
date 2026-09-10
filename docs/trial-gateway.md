# Argus hosted trial

The trial is distributed privately by the operator. The pool contains exactly
**10 API keys**, each with **1,000,000 lifetime input + output tokens**. The same
key shares its balance across devices. No machine fingerprint or coding-account
login is required.

At the user's request, public EN/zh trial pages, homepage navigation links,
`/trial/claim` and `/trial/session` were withdrawn on 2026-09-10. There is no
public key issuance or cookie recovery. Authenticated model/status APIs and the
installation wheel remain available at `https://argusbot.cn`.

## Desktop setup

The desktop source now includes a private-key entry on first-run settings and
the startup screen for Mac and Windows. Submitting a key validates it, downloads
and verifies official standalone Copilot, checks a real agent reply, then opens
the bundled cockpit. No terminal, Python, Node.js or coding-account login is
required by this path. Successful configuration persists across restarts.
See [desktop trial setup and builds](desktop-trial.md).

This requires a newly built desktop package. Mac/Windows native installer and GUI
verification remains untested; no updated installer has been published yet.

## Command-line setup

Requires Python 3.11+ and Node.js 22+/npm. Obtain a private key from the operator, then:

```bash
python -m pip install --force-reinstall https://argusbot.cn/trial/downloads/argus_skill-0.1.1-py3-none-any.whl
argus --setup --trial
argus
```

Paste the key at the hidden prompt. Setup installs or updates GitHub Copilot CLI
when needed, configures its official custom-provider (BYOK) support, and checks
one real agent reply before saving trial mode. No GitHub/Codex/Claude login is
needed. For automation, supply `ARGUS_TRIAL_KEY` through a private environment;
noninteractive setup never prompts. `--trial-url HTTPS_ORIGIN` supports a separate
gateway. Installation uses `--force-reinstall` because this unpublished build
shares the base package's 0.1.1 version.

The existing public wheel is still the command-line package. Merely downloading
it does not auto-install Copilot; `argus --setup --trial` triggers configuration.
Its current deployment has not been replaced by the new desktop source.

Only the user's trial key is saved in `~/.argus-skill/copilot-trial.json` (0600).
One-shot workers and persistent Manager ACP sessions use the gateway and a
separate `copilot-trial-home`. Their tools run on the user's project locally.
A failed setup restores the previous trial profile. A successful regular
`argus --setup --backend copilot` switches back to the user's own account.
Explicit project/role overrides retain precedence; remove them when converting
an existing project. Trial usage counts tokens but has zero user dollar cost;
it does not wait for personal Copilot billing reconciliation.

The trial provider is **`gpt-5.5` with reasoning effort `high`**, exposed as
`argus-trial`. Clients retain the text/tool Chat Completions contract. The gateway
translates requests, streamed text and local function/custom calls to Copilot `/responses`
(the model rejects `/chat/completions`). Model and high effort are enforced on
the server for existing clients too; there is no fallback to GPT-4.1. New desktop
setup persists GPT-5.5 and high role efforts. Images, embeddings and Anthropic
wire requests remain unsupported. Argus sends `User-Agent: Argus/0.1.1` for status and
Copilot BYOK requests: the existing Cloudflare site rejects the CLI's default
agent header with HTTP 403. No browser challenge is needed with the Argus header.

## Private key distribution

The user distributes the ten keys directly to invited testers. Downloaded
packages never contain trial keys or upstream credentials. Preserve the existing
ledger when changing distribution: closing a page must not reset user balances.

Issue or re-export the pool on 111:

```bash
cd ~/argus-trial-gateway-20260909
.venv/bin/argus-trial-server issue-keys
```

The command preserves the ten stable keys and balances. It prints only
the export path: `~/.local/share/argus-trial-gateway/trial-keys.json` (0600),
**outside the served directory**. The database stores credential hashes and retains historical claim records.
Do not publish the operator export. The former browser recovery/claim endpoints
are absent from the gateway even if an old static page remains on disk.

## Server limits

| Scope | Limit | Rejection |
|---|---|---|
| Each key | 1,000,000 lifetime input + output tokens | 402 `trial_quota_exceeded` |
| All keys | 10 active model requests | 429 `trial_busy` |
| All keys | 10,000,000 tokens per rolling 60 seconds | 429 `trial_tpm_exceeded` |

Idle apps do not use slots. Limits are checked in one SQLite transaction before
forwarding. Admission reserves conservative UTF-8 input bytes plus protocol/tool
framing and the enforced output maximum (at most 16,384 tokens). Actual upstream
`input_tokens + output_tokens` (mapped to chat usage fields) settles lifetime usage.
Output includes reasoning tokens; they are not counted twice. Cached input is
counted once. Unknown usage from disconnects, timeouts or process failure keeps
the reservation. No prompt is truncated. The byte reservation is conservative,
not an exact tokenizer; enforcement assumes the provider honors the output cap
and token accounting. Any reported overrun is recorded rather than hidden.

TPM holds the full reservation throughout execution and for 60 seconds after
completion, including across restart. Proven zero-use failures release it.
The ledger is authoritative; client changes do not increase quotas. Exactly one
gateway process owns it, enforced by a process lock. Limits cover traffic through
this gateway; unrelated use of the same upstream account is outside its meter.

Authenticated `GET /trial/status` returns lifetime usage/remaining tokens, active
requests and global TPM counters. In-flight reservations count as used. A smaller
prompt/output request may still fit near the allowance limit.

## Client compatibility

### Pi custom-provider use

The desktop trial setup configures Copilot. Pi can independently use the same
gateway through its existing custom-provider configuration; this is not an
additional one-click desktop backend.

In Pi's `models.json`, configure:

```json
{
  "providers": {
    "argus-trial": {
      "baseUrl": "https://argusbot.cn/v1",
      "api": "openai-completions",
      "apiKey": "$ARGUS_TRIAL_KEY",
      "headers": {"User-Agent": "Argus/0.1.1"},
      "models": [{"id": "argus-trial", "reasoning": true}]
    }
  }
}
```

Supply the invitation key privately in `ARGUS_TRIAL_KEY`, then select
`pi --provider argus-trial --model argus-trial --thinking high`.
The public gateway accepts Chat Completions and translates to Responses itself;
do not point Pi's `openai-responses` adapter at this endpoint. Model and effort
remain server-controlled. Pi JSON mode can exit zero on a provider error, so
consumers must inspect `stopReason` / `errorMessage`; Argus already does.

Copilot ACP can similarly report a query error as assistant text followed by
`end_turn`. Argus verifies that against the current Copilot `session.error`
receipt rather than counting the error as a completed answer or premium request.

## Deployment on 111 and Cloudflare

Deployment: `~/argus-trial-gateway-20260909`, isolated `.venv`, user service
`argus-trial-gateway.service`, loopback `127.0.0.1:18765`. Existing named tunnel
`argus-website-prod` publishes through `argus-website-tunnel.service`.
Config: `~/.cloudflared/argus-website-prod.yml`.

Before each host's ordinary website rule, route only these paths for
`argusbot.cn` and `www.argusbot.cn` to `http://127.0.0.1:18765`:

```yaml
path: ^/(trial/(status|downloads/.*)|v1/(models|chat/completions))$
```

After that allow rule, an explicit `http_status:404` rule for
`^/(trial(/.*)?|zh/trial(/.*)?)$` withdraws old page, claim and recovery URLs.
Other website traffic retains `127.0.0.1:8792`. Both the tunnel and gateway now
reject old public claim endpoints. The gateway serves only the isolated
`~/argus-trial-gateway-20260909/site/trial/downloads` directory as static files.

EN/zh homepage links were removed from the live
`~/argus-website-public-smooth-theme-20260909` snapshot and source
`~/argus-website/src/components/Header.astro`. Other website content was preserved.
Withdrawn pages and configuration backups are outside the public root, under
`~/argus-trial-backups/private-distribution-20260910T080430Z`.

## Upstream credential and recovery

The gateway reuses the selected login from **111's Copilot CLI**. The CLI is the
login source; the gateway performs HTTP forwarding and metering. It uses the
same fixed `https://api.githubcopilot.com` origin and `copilot-developer-cli`
integration. The editor v2 token exchange is not used.

To change the upstream account, log in with `copilot login` on 111, then:

```bash
cd ~/argus-trial-gateway-20260909
.venv/bin/argus-trial-server import-copilot-login
systemctl --user restart argus-trial-gateway
```

Import verifies access and restores the previous vault if verification fails.
The gateway copy is Fernet-encrypted at
`~/.local/share/argus-trial-gateway/github-token.enc`; its separate master key is
`~/.config/argus-trial-gateway/master.key`. Both are 0600, with 0700 directories.
The original CLI config is unchanged. Upstream tokens, response headers and raw
provider errors are never returned to clients. Prompts/outputs are not persisted
by the gateway. Encryption at rest does not protect against an operator who can
read the master key or a compromised server process.

Back up the database and encrypted credential consistently, with a separate
protected master-key backup. Do not delete the ledger or casually rotate the
master key: it also derives the ten stable trial keys. `init` preserves an
existing key and rejects existing state whose master key is missing.

Pre-launch rollback backup:
`~/argus-trial-backups/key-launch-20260910` (old wheel, service, tunnel config,
homepages, source header and consistent ledger snapshot). The old build is the
superseded machine-based trial; do not restore its public registration as an
unreviewed rollback. Current deployment updates preserve all historical usage.

## Verification and current state

The prior integrated trial release passed 417 related tests and real public
setup/model/local-tool calls. After withdrawal, all 63 trial tests passed,
including removed claim/recovery routes, old static pages remaining inaccessible,
private-key authentication, issuance races, quotas, TPM and real Copilot CLI with
a simulated upstream. Ruff and diff checks pass. Apex/www page/session/claim URLs
return 404, both homepage links are absent, authenticated status remains active
and the public wheel still downloads.

The existing key pool and usage were preserved. Key 1 had one public claim record
and no token usage at withdrawal; its claimant is not yet confirmed. Key 10 has
21,379 smoke tokens charged and 978,621 remaining after desktop frozen-runtime
verification; other balances are 1,000,000.
Historical machine usage (8 calls / 21,161 tokens) remains as audit. Old machine
credentials remain invalid. No keys were reset or added.

Source: `/data/yijia/argus-trial-20260909`, branch
`feat/trial-gateway-20260909`, based on fetched/pulled `origin/main@af3b5a995`.
Source changes remain uncommitted and unpushed.

Private-distribution wheel SHA-256:
`b66fda3160b4f561c5b21a6d39e64a004b81eca8882952a97204d8ff2876e50c`.
