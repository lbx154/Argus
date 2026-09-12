# Hosted research trial and data review

The hosted service provides ten invitation-scoped workspaces, separate model and
compute accounting, an operator dashboard, and consent-aware research replay and
dataset export. It runs on a Linux operator host; ordinary Argus desktop and Web
installations do not enable this service automatically.

## Mission map

The configured hosted frontend is shared by authenticated invitation and
administrator workspaces. Their project APIs remain separately routed; sharing
the interface does not merge projects, credentials, or conversation records.

The map shows the latest page of each mission as a single card by default.
**History** expands its earlier pages without creating new tasks or removing
events. Grouping follows durable task IDs, not similar titles. **Locate current**
opens the latest page; the part controls navigate the complete history. Running
team workers retain their actual state even on an earlier page or while the
parent mission is paused. This display change does not alter recorded training
data.

Frontend-only releases preserve embedded HTML previews on older tenant APIs,
with a visible limitation notice. The standalone interactive preview is used
when the preview response advertises `served_page: true`.

## Data and metric contracts

The dashboard separates successful HTTP message submissions, explicitly accepted
tasks, and compute submissions. A chat reply, a business error returned with HTTP
200, a duplicate dispatch, or an unidentified task is not an accepted task.
Captured task IDs connect a request to its response and matching runtime events;
nearby timestamps are never used as task identity evidence.

New hosted configurations mark all ten invitations as internal team testing, so
their activity is excluded from external traction by default. Operators must
explicitly mark genuine external trials in `analytics.json`. Re-initialization
preserves an existing analytics configuration and does not change account roles
or permissions.

Model accounting is cumulative, independently of the dashboard date filter:

- **Settled:** token usage reported by completed provider calls.
- **Reserved:** outstanding reservations for active requests.
- **Uncertain:** charges retained after unknown or interrupted usage.
- **Unattributed:** ledger balance without corresponding request receipts.

Unavailable values remain unknown. The total ledger balance includes reservations
and must not be presented as entirely measured token consumption. A hosted
`token_limit: null` means unlimited cumulative allowance; concurrency and TPM
admission still apply. The separate desktop trial keeps its own default allowance.

## Authorization and replay

The current research notice and training purposes are versioned. The portal can
record affirmative onboarding acceptance, or an operator can explicitly record an
existing team policy through the separate offline-authorization mechanism. An
offline policy is identified as operator-attested authorization, not a fabricated
browser receipt. An unknown historical effective date does not authorize backfill.

Internal training and external sharing are separate purposes. External sharing is
off by default and needs its own authorization and export review. Revocation,
notice changes and project deletion are checked again during collection/export.

The persistent collector works without a browser tab. It records selected public
HTTP and runtime observations, with stable identities, ingestion sequence,
truncation and gap markers. Initial observation and source replacement establish
a collection boundary; existing bytes are not retrospectively imported.
Sequence is ingestion order, not a proof of causal order or successful work.

Project research copies can be deleted with a tombstone that disables future
collection for that project. Research retention is at most 30 days, with additional
capacity limits. Source workspace/runtime files, accounting, separately retained
consent/checkpoint metadata, downloaded exports and independent backups have
separate lifecycles. Logical deletion is not secure disk erasure.

## Dataset review

The operator page at `/admin` supports selected-project previews, purpose filters,
pagination, explicit per-event quality review, and export auditing. The tester data
page explains the current permissions, recorded authorization and revocation
controls. Previewing candidates does not automatically approve them for training.

The pinned Pi observer records actual public context, tool definitions, provider
request projections, model arguments, executed arguments and tool results.
Provider strict-schema conversion and optional-null argument removal are checked
against the pinned runtime behavior. Generated comparison values never replace
the captured provider schema or model arguments in training samples. Unsupported
coercions, incomplete/truncated tool episodes and sensitive content are quarantined.

The observer excludes system instructions, hidden reasoning, private signatures
and credentials. A complete public tool episode is not the complete model context.
Tool SFT export additionally requires explicit review that the public episode is
self-contained. Exports distinguish `human_operator`, `automated_acceptance` and
`unspecified` review; automated acceptance includes an evidence digest and is not
reported as human review. No dataset is uploaded or training job started by export.

Per-event review receipts bind the selected purpose, event, sample content and
reviewer attribution. Later exports keep each sample's original review and
evidence, even when a different reviewer creates the export.

## Offline package validation

After downloading a dataset ZIP, validate it locally with the standalone module:

```sh
python -m argus_skill.trial.training_validate /path/to/dataset.zip \
  --require-agentic \
  --evidence /path/to/acceptance-report.json \
  --report /path/to/validation-report.json
```

`--evidence` supplies the exact original file whose SHA-256 was recorded during
automated acceptance. Repeat it for samples reviewed against different reports.
Every automated export or sample review requires its matching evidence file;
declaring an evidence digest alone is insufficient. Human reviews without an
evidence digest do not require this option.

The command reads the current ten-file export ZIP directly and checks its file
set, sizes, hashes, JSON/JSONL structure, tool schemas and arguments, captured
call/result correspondence, sample splits, counts and review attribution. It
does not extract files, call tools, contact a model or launch training. Packages
and their total uncompressed contents are limited to 64 MiB; up to 256 evidence
files are accepted, each at most 32 MiB.

The JSON report always appears on standard output. `--report` also writes the
same report atomically to a separate file and cannot overwrite an input package
or evidence file. Reports contain hashes, counts, fixed error codes and locations;
they do not repeat sample text or tool arguments.

Exit status `0` means `valid: true` and, when `--require-agentic` is present,
`agentic_training_ready: true`. Status `1` means validation failed or the required
reviewed tool samples are absent; status `2` identifies invalid CLI arguments.
A valid chat-only or empty package is not ready for agentic tool training.
Keep `--require-agentic` when this distinction must fail a pipeline. An empty
validation split can be intentional for a single tester group.

The report establishes package consistency and correspondence to the included
observations. It does not prove that the runtime was untampered, that declared
human review occurred, or that scientific conclusions, proofs, benchmarks and
licensing claims are correct. Public episodes omit system instructions and
private reasoning. Keep the original evidence and deployment/task receipts for
separate verification, and evaluate exported samples for the intended model and
chat template before training.

## Operator setup

Keep deployment state and generated credentials outside the source checkout.
Examples below use `ARGUS_TRIAL_ROOT` for an operator-chosen absolute directory.
No operator home directory, credentials, workspace or private receipt belongs in
a container build context or Git commit.

Install Python dependencies with `python -m pip install -e '.[trial]'`. Build the
ordinary Web bundle with `npm --prefix frontend/web ci` and
`npm --prefix frontend/web run build`. A separate hosted build can use
`VITE_ARGUS_HOSTED_TRIAL=1` and `--outDir "$ARGUS_TRIAL_ROOT/frontend"`; point
`portal.json`'s optional `frontend_dir` to that directory. Keep hosted build
artifacts separate from the checked-in default desktop/Web bundle.

Build subsequent hosted releases into new versioned directories, preserve
previously served hashed assets there for existing browser sessions, then switch
`frontend_dir` to the completed release. Do not rebuild or empty the directory
currently serving browser requests.

When HTTPS terminates at a reverse proxy, set `portal.json`'s `public_origin` to
the exact external HTTPS origin, for example `https://example.com`. The actual
request Host must match that configured authority. Client-provided forwarded
headers do not establish trust. Without this setting, strict same-origin checks
remain in effect, including direct local access.

Initialize with the existing private administrator backend's token file:

```sh
python -m argus_skill.trial.web_admin init \
  --root "$ARGUS_TRIAL_ROOT" \
  --admin-token-file /path/to/private/backend-token \
  --admin-url http://127.0.0.1:8896
```

Initialization preserves existing keys and balances and generates a distinct
public administrator login credential. It does not import the provider login or
start containers. Import provider authorization separately through the existing
`argus-trial-server` operator commands. Configure storage ownership for the actual
deployment user before mounting quota-backed tenant filesystems.

Build `deploy/trial/web.Dockerfile` with a named `runtime-tools` context containing
the standalone CLI. Build `pi.Dockerfile` with `argus-pi` and `node-runtime` named
contexts containing the pinned Pi distribution and Node binary. The hosted
observer profile requires Pi 0.85.1 and its checked runtime implementation; arbitrary
distributions are not automatically trusted. `pi.Dockerfile` and
`compute.Dockerfile` accept `--build-arg WEB_BASE_IMAGE=YOUR_WEB_IMAGE` so each layer
can reference the base image built from the same source and Python dependencies.
Set the generated deployment configurations to the selected image tags.

Start newly provisioned workspace containers with the capture image:

```sh
python -m argus_skill.trial.web_admin start-containers \
  --root "$ARGUS_TRIAL_ROOT" \
  --image argus-web-trial:pi-data-20260911-r6
```

The same capture tag is the default when `--image` is omitted. Pass an explicit
tag for a later verified release. The separate compute scheduler uses its own
compute image from `compute.json`.

Existing containers retain their image: this command starts them without
recreating them, and Docker restart also retains their original image. Upgrading
them requires a separate migration after their active and queued tasks have
finished. Preserve each tenant's data, bootstrap and socket mounts, retain a
rollback container, then recreate only the idle container with the selected
image. Do not replace an active container to enable capture; collect new tasks
after its migration instead of importing earlier runtime logs.

`deploy/trial/web_services.py --root "$ARGUS_TRIAL_ROOT"` creates five user units:
the portal, model meter, compute scheduler, HTTPS egress proxy and relay guardian.
It does not install or take over an unrelated legacy demo service. The provided
`argus-trial.slice` is an aggregate resource ceiling template; review its CPU and
memory values against the deployment host before installing it. Storage mount
units and reverse-proxy configuration are operator-specific and are not bundled
with private machine paths.

The default compute configuration uses a shared GPU pool, per-job reservations
and 200 GPU-hours per invitation. Queued time is not execution time. Every compute
status/log/cancel request checks the job owner; a job ID alone grants no access.
Workspace and compute containers use a read-only root filesystem, separate tenant
volumes, no Docker socket and private Unix-socket forwarding. HTTPS egress rejects
private/non-public destinations. Existing host work must not be terminated to make
capacity available for the trial.

## Capture capacity and public references

Hosted Pi episodes retain at most 16 MiB of cumulative observations and 512
observations. Repeated public contexts count toward that total. Each projected
event and observer RPC remains limited to 4 MiB, with an 8 KiB transport envelope;
individual public text stays at 256 KiB and tool results at 64 KiB. Retained hosted
episodes share the existing 128 MiB storage budget. Exceeding a limit quarantines
the episode; it never silently truncates a training sample.

Preview/source selection and generated export packages are each capped at
32 MiB. Export checks both expanded contents and ZIP bytes. Export large projects
separately when their combined package exceeds the limit. The offline validator
retains its independent 64 MiB ZIP and expanded-content ceiling; that does not
increase the exporter limit.

Standard platform directory references are recognized as public path literals,
with project and mission references bound to the observed runtime. Published
Skill files are resolved from the built-in package inventory, and actual `read`
results must match the shipped body or its exact requested line selection.
Unknown or modified global files and raw model logs are not admitted by these
exceptions. LaTeX escapes are distinguished from actual UNC server/share paths;
Windows drive and device paths remain sensitive. Credential/environment dumps
continue to be filtered or quarantined. Quarantines retain only fixed event kind,
structural field and detector labels for diagnosis, never matched content.

## Validation

For repeatable ordinary-user production tasks and the independent acceptance
workflow, see [Agentic training examples](examples/agentic-training/README.md).

Linux CI installs `.[dev,qr,trial]`, runs Ruff and the full Python suite. Web CI
typechecks and tests the current Web frontend. Local focused validation is:

```sh
python -m ruff check argus_skill/trial tests/trial
python -m pytest tests/trial tests/test_pi_backend.py
```

The actual Pi CLI test is opt-in with `ARGUS_TEST_PI_DIR` pointing to the pinned
local Pi checkout. It uses a synthetic local provider and temporary workspace,
not production credentials or user tasks. Production acceptance must separately
verify the deployed source/image, ordinary invitation routes, actual tool/artifact
results, correct project association and truthful export/review metadata.
