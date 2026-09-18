# PR Gate

This package contains the existing PR-description gate and the local regression
gate. The description gate is a lightweight lexical contributor prompt. It checks whether the
combined pull request title and body contain enough detail for the patch size
and mention any changed test, documentation, or configuration categories.

These local checks do not establish semantic consistency. A sufficiently long
but irrelevant message can satisfy lexical scope scoring, and category checks
only establish that recognized category words are present. The next PR gate
version will leave semantic consistency checking to an LLM-based criterion.

The current version is calibrated for English descriptions. In the next
version, an LLM will translate non-English descriptions into English, preserve
both the original and translated text as evidence, and run the translation
through the same criteria used for English descriptions.

Scope scoring uses a continuous description-length ratio relative to text
churn. File-category scoring includes only recognized changed categories;
unknown files do not affect its score.

Binary files currently contribute `0.0` lines of text churn because Git does
not provide meaningful added or deleted line counts for them. A binary-only
patch therefore makes lexical scope scoring not applicable and does not fail
the gate. This is an intentional lightweight policy rather than an estimate of
binary change size.

Malformed event payloads fail closed with a structured `pr-gate/1.0` result,
a GitHub error annotation, and a non-zero exit status.

## LLM interface

The optional `LLMClient` interface keeps provider execution separate from
criterion decisions. `CopilotCLIClient` invokes `copilot -p` non-interactively
with an empty tool set, built-in MCP servers and custom instructions disabled,
remote session features disabled, and authentication variables hidden from any
child tool environment.

Prompts contain bounded description, patch-summary, and patch-diff fields.
Responses must match the strict `LLMJudgment` JSON schema. The local gate, not
the Copilot process exit code, applies criterion thresholds. Missing clients,
timeouts, CLI failures, oversized output, and invalid responses mark enabled
LLM criteria as unavailable and make the overall result `incomplete`.
Process supervision bounds both execution and output draining; inherited
stdout/stderr in child processes cannot indefinitely extend the configured
timeout. Cleanup failures are surfaced instead of returning a successful score.

## Local regression gate

```bash
python -m argus.release_tools.pr_gate check
python -m argus.release_tools.pr_gate verify
```

`check` analyzes staged source with Copilot plus the regression Skill and writes
one `pr-regression-report.json`. `verify` validates its source/policy binding and
embedded evidence without invoking a model, probe, or network. The source-checkout
launcher `./scripts/pr-gate` and installed `pr-gate` command use the same entry
point. Native execution is the default; Docker is optional.

The local v2 contract binds changed-file inspection to actual Git entries,
requires a common Python command and frozen oracle inputs for paired probes,
and checks observed project source against its assigned root/src snapshot.
Non-Python snapshot data reads must be covered by the oracle manifest; omitted
inputs make observations incomplete. The conservative rule also covers product
configuration, so changed configuration can require an uncertain result.
Unsupported provenance and incomplete coverage cannot pass. Publication checks
freshness under a repository lock before replacing an existing report, so a
stale analysis does not overwrite a newer report. Old v1 evidence must be
regenerated.

All implementation and runtime assets live here:

| Location | Responsibility |
|---|---|
| `config.py`, `criteria.py`, `patch.py`, `llm.py` | Description gate and optional LLM interface |
| `owned_process.py` | Shared bounded command execution and descendant cleanup |
| `regression/cli.py`, `snapshot.py` | Local commands, staged-source binding and report publication |
| `regression/evidence.py`, `validation.py`, `scope.py` | Embedded report, receipt, source and coverage validation |
| `regression/runner.py`, `runtime.py`, `entry.py` | Native/optional Docker Copilot execution |
| `regression/probe.py`, `oracle.py`, `origin_guard/` | Paired probes, common-oracle inputs and Python source origins |
| `regression/SKILL.md`, schemas, Docker assets | Packaged execution policy and resources |
| `regression/study.py`, `study_summary.py` | Separate historical experiment orchestration and artifact audit |

`experiments/pr_regression_50/` contains only compatibility launchers and study
documentation; archived runs remain unchanged. Native execution is not an OS
sandbox, identical test inputs do not prove oracle correctness, and coverage
declarations do not prove semantic understanding.

See [the local gate guide](../../../docs/local-pr-regression-gate.md).
The existing `--event ... --config ... --output ...` description-gate interface
is preserved. No hooks or CI enforcement are installed automatically.
