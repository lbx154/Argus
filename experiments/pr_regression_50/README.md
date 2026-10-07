# Copilot-only historical Argus PR regression study

This exploratory study analyzes 50 historical Argus PRs with one frozen Skill
and one Copilot session per PR. It does not start an Argus daemon, delegate to
other agents, or run additional models.
The initial run pins Copilot CLI 1.0.85 and `gpt-6-astra` with high reasoning
effort. The executable is copied into the study before dispatch.

The maintained implementation and runtime assets now live under
`argus/release_tools/pr_gate/regression/`. This directory keeps only the
`run.py` and `summarize.py` forwarding entry points and this study description.
Previously frozen runners/results are not migrated or rewritten.

The reusable runner also supports a
[single-command local gate prototype](../../docs/local-pr-regression-gate.md):
`./scripts/pr-gate check` and `./scripts/pr-gate verify`. That prototype is
native by default; Docker is optional. It does not change the frozen 50-PR run.

## Sampling and comparison

Inventory all public PRs at collection time. Eligible PRs are merged into main,
have a two-parent merge matching the recorded PR head, and are reachable from
the frozen main tip. Select 10 uniformly sampled PRs from each of five
equal-count chronological strata, using seed `20260916`. Record all exclusions.
Compare the actual merge's first parent with the merge, not today's main.
PR descriptions are current API snapshots and are not normative ground truth.

## Isolation and bounds

- At most five concurrent Copilot containers; two CPUs, 4 GiB RAM, and 256
  processes per worker, with a 20-minute analysis deadline.
- Dedicated study directory, private base/candidate Git snapshots, per-worker
  HOME/COPILOT_HOME, read-only root filesystem, no capabilities or privilege
  escalation, and no Docker socket or real host home mounted.
- Snapshot preparation has no network. Analysis uses an internal Docker
  network with a separate HTTPS CONNECT proxy allowing only GitHub/Copilot
  service endpoints. No host ports are published.
- Only local shell/file tools are exposed. Built-in MCP, custom instructions,
  memory in prompt mode, remote export, and auto-update are not enabled.
- Credentials come only from the active Copilot account, are supplied by
  environment without appearing in command arguments, and are stripped from
  shell tool environments. Credential files are not copied into the study.
- Deterministic paired probes have fresh state roots, no inherited provider
  credentials/proxy configuration, and 120 seconds per revision.
- Each worker uses `--rm`; only this study's exact labeled resources are
  removed. Source checkouts are deleted after evidence is retained. The
  dedicated image is removed after the batch finishes; no global prune.

The containers share host hardware, so compute contention is bounded, not
eliminated. The proxy is infrastructure, not an additional agent. Historical
source and generated probes remain untrusted code inside the worker boundary.

## Verdict semantics

Negligible runtime-impact changes can short-circuit with an evidence-backed
`NO_REGRESSION_FOUND` without a test. Executable Markdown does not qualify
merely because it is a documentation file. Model-mediated changes that cannot
be settled cheaply remain `UNCERTAIN`.

The host validates schema, revision identity, evidence paths/digests, paired
receipts for regression claims, and that tracked source remains unchanged.
Git inspection of worker-writable repositories runs in an offline container,
never on the host. Session logs must show the frozen Skill as the first tool
read and only the allowed local tools.
These validations do not independently establish the semantic correctness of
a Copilot-authored oracle. Findings still need human adjudication; this study
does not measure precision/recall without independent labels.

## Execution

```bash
docker build -q --label copilot.study=pr-regression-50-20260916 \
  -t argus-pr-regression:20260916-s391859ca argus/release_tools/pr_gate/regression
python experiments/pr_regression_50/run.py prepare --root /path/to/new-study
python /path/to/new-study/runner/run.py smoke --root /path/to/new-study
python /path/to/new-study/runner/run.py run --root /path/to/new-study --workers 5
```

Use a new study directory; do not rerun these commands against the completed
2026-09-16 archive. `python -m argus.release_tools.pr_gate.regression.study`
provides the same actions. The local v2 gate's stricter source/oracle/coverage
requirements apply only to local checks, not retroactively to historical reports.

The durable root contains `manifest.json`, `population.json`, `selection.json`,
the frozen Skill/runner, `inputs/pr-N/`, `results/pr-N/`, and `summary.json`.
`runner-result.json` distinguishes completed reports from timeouts, invalid
reports, cancellations, and invocation failures. No missing report is counted
as a pass. A lock prevents two schedulers from sharing this study root.

## Post-hoc artifact audit

The initial frozen runner rejected container-absolute `/work/...` evidence
paths and reports declaring overall uncertainty alongside a supported local
regression. The maintained validator handles both without changing the model's
verdict. `summarize.py --output <new-directory>` audits all retained reports
into a separate directory, retaining the original runner status and hashes.
It does not rerun models or probes, rewrite the original run, or retrospectively
perform source-integrity audits skipped after the nine initial rejections.
Artifact consistency is not independent semantic adjudication.
