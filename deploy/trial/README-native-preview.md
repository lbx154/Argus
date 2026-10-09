# Invitation preview with GPT-6.1 Sol

This is a Linux operator deployment of the real Argus web application. Five
invitations select five separate runtimes and workspaces. Each invitation has
$10 of model allowance; the entire deployment has $50, with no daily reset.
Copilot CLI uses its Responses BYOK provider against an operator-owned meter.
The operator's GitHub login remains outside every runtime.

## Requirements and initialization

Use a pinned checkout with rebuilt release artifacts, Python 3.11+, Argus's
`trial` extra, Node, Copilot CLI with BYOK Responses support, bubblewrap and a
working systemd user manager. Warm up Copilot CLI on the operator account first;
only its extracted `~/.cache/copilot/pkg` packages are mounted into guests.
Do not mount the rest of the operator cache or home. The reference deployment
uses Copilot CLI 1.0.94 and Python 3.12.

Run `native_setup.py --help` for the required paths. For example:

```sh
.venv/bin/python deploy/trial/native_setup.py \
  --root /srv/private-argus-preview \
  --source /srv/argus-release \
  --venv /srv/argus-release/.venv \
  --python-runtime /opt/python-3.12 \
  --node-runtime /opt/node \
  --copilot-package "$HOME/.cache/copilot/pkg" \
  --revision FULL_GIT_SHA --install-services
```

Keep the private root outside Git. `preview.json` and `invitations.txt` are
operator-only files. Reinitializing an existing root preserves invitations,
charges and limits. To upgrade, stop the services, update `source` and `revision`
to a checked, pushed commit, reinstall service definitions and restart. Never
recreate the ledger to replenish a trial.

The meter serializes admission in SQLite, reserves conservative text input and
capped output cost before submission, and settles from terminal Copilot
`copilot_usage.total_nano_aiu` receipts. Reservations use twice the observed
GPT-6.1 tariff (input/cache-write at most $2.50/M, output $10/M). Missing receipts
or interrupted submissions retain their reservation. A single locked meter
recovers unfinished requests on restart; portal restarts do not alter charges.
Hosted tools and remote media references are rejected because their billing
cannot be bounded from text bytes. Reservations can pause a request before all
nominal balance has been spent. Confirm tariffs before enabling a new provider
version. There are at most two simultaneous upstream model requests.

## Runtime boundaries

Bubblewrap creates separate user, PID, mount, IPC, UTS and network namespaces.
Only that invitation's state/home/workspace is writable. Language runtimes and
source are read-only; the host home and other invitations are absent. Guests
have no host network. Local socket bridges provide the metered model endpoint
and an HTTP proxy that resolves, validates and pins public IP destinations on
ports 80/443, rejecting private networks and metadata endpoints. Shell tools
that use the configured HTTP/HTTPS proxies can access public internet resources.
Tools needing direct sockets are unavailable.

The user services request 8 GiB memory, one CPU and 512 task limits per runtime.
Some user managers have no delegated resource controllers. An independent
host-side watchdog always samples aggregate process-tree RSS and thread counts
at 0.5-second intervals, pausing above 8 GiB / 512 threads. A per-runtime CPU
core is assigned before startup and the watchdog reasserts every thread's CPU
affinity, allowing normal work to finish within its capacity. Memory/process
checks allow short bursts and are not equivalent to kernel cgroup quotas.
Operator-only logs are retained in the private deployment directory.
Keep at most two warm Manager contexts, expiring inactive contexts after three
minutes. Native Copilot clients for classification, tools and multiple roles
can exceed 2 GiB in ordinary use; the 8 GiB allowance accommodates this without
changing the $50 model budget. Memory/thread stops exit with temporary-failure
status 75 so systemd restarts the runtime after five seconds (up to four starts
per minute). Storage stops require operator cleanup. Files and the model ledger
survive these restarts. The portal displays a retrying page while the runtime
is unavailable. Root and device directories are
read-only; temporary/shared-memory files use each tenant's monitored disk
directories. Files have a 32 MiB size limit. A 2 GiB / 100,000-entry workspace monitor pauses
an oversized runtime; this monitor is not an operating-system disk quota.
Invitation sessions use signed, secure, HttpOnly cookies. HTTP mutations and
browser WebSockets check the browser origin. Runtime upgrades and provider /
budget settings are operator-managed. Model credentials inside guests authorize
only their own bounded balance.

## Cloudflare

Point a Cloudflare tunnel at the loopback portal on port 18871. Deploy
`cloudflare-preview-worker.mjs` with `ORIGIN` set to the tunnel's HTTPS URL and
`RELAY_SECRET` supplied as a Worker secret matching the private config. Capture
Wrangler's output privately because temporary deployments print a claim link.

The Worker relays HTTP through an authenticated origin WebSocket so streamed
responses work with Quick Tunnels; browser WebSockets are forwarded separately.
The HTTP body limit is 16 MiB. Verify login, assets, SSE, browser WebSockets and a
real file-producing task on the public URL. A temporary Cloudflare account
expires; a Quick Tunnel address can change after restarting cloudflared. Update
`ORIGIN` after such a change. Use an authenticated account and named tunnel for
a permanent deployment.

Check `/healthz` for the pinned revision and authenticated `/trial/budget` for
actual remaining allowance. Stop the preview with
`systemctl --user stop 'argus-preview-*'`. The private ledger survives shutdown.

### Direct Quick Tunnel access

The portal also supports using the Quick Tunnel URL directly, without a Worker
or a temporary account. `trial-transport.js` wraps only the Manager's streamed
POST endpoint in an authenticated, same-origin browser WebSocket, restoring
streamed replies despite Quick Tunnels' SSE limitation. Other browser requests
use ordinary HTTP. The portal supplies the real session cookie to the internal
request; browser frames cannot select a different tenant or an arbitrary API.
This URL stays usable while the tunnel process runs, and does not inherit the
one-hour expiry of Wrangler 4.149's temporary accounts. Restarting the tunnel
can change the address. Non-browser clients must use the authenticated
`/trial/stream` protocol or a named tunnel / Worker for streamed requests.
All main and auxiliary model routes are pinned to GPT-6.1 Sol, including front
door classification, bounded planning, plan preview and prompt rewriting.
