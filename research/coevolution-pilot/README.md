# Argus / Argus-Pi co-evolution pilot

This is the 2026-09-14 five-task [SkillsBench](https://github.com/benchflow-ai/skillsbench) experiment. The model produced Skills, Wiki pages and an inspection helper that was validated, activated and used in the same running Pi session. **Heldout task reward remained 1/3 in all four conditions.** Joint use reduced calls from 33 to 23, tokens by 2.8%, and estimated cost by 0.9%. One run per task/condition does not establish a quality or cost advantage.

Read the [Chinese report](report.md), open the standalone [interactive report](report.html), or inspect the [comparison chart](ablation.png). Original verifier scores remain unchanged, including the [reference inconsistency](benchmark-audit.json). The known defects in the generated Skills and Wiki are preserved.

## Implementation and evidence

- [run_experiment.py](run_experiment.py) reuses Argus Engineer/Manager prompt contracts, SkillStore and WikiStore in a bounded controller. It does not exercise every production daemon, retrieval or propagation path.
- [harness/runtime-extension.mjs](harness/runtime-extension.mjs) records observations and admits a model-authored Python helper after [format contract checks](harness/contract_check.py). Acceptance enables `inspect_data` immediately in the same session. This extends execution tools; it does not train weights or patch the production Pi kernel.
- [learning/frozen](learning/frozen) contains the exact generated Skills, Wiki, helper and its version history. [learning/snapshots](learning/snapshots) retains intermediate artifacts. Knowledge conditions inject all frozen documents; heldout runs cannot update learning.
- [results.json](results.json), [scores.csv](scores.csv), `runs/*/grading/pytest.txt` and `oracle-checks/` preserve all included scores. `setup-invalid-runs/` retains the excluded controller setup failure.
- `runs/*/trajectory.jsonl.gz` preserves each original Pi event stream without loss. `runs/*/agent/runtime-events.jsonl` records actual activation and use. Decompress a trace with `gzip -dc PATH/trajectory.jsonl.gz`.
- [gateway-usage.jsonl](gateway-usage.jsonl) accounts for 242 experiment-agent requests, about $7.239 estimated in total. It excludes the assistant conversation and unrelated production traffic; it is not a GitHub billing statement.

The helper contract is a small format check, not a security sandbox or benchmark score. The experiment's Bash command guard is not a general defense against recursive agent spawning. Agent work runs sequentially in disposable, network-disabled containers; the model gateway alone forwards requests. Reference answers and verifiers are mounted only for oracle checks and separate grading, never in agent containers.

## Verify this published result without model calls

Requirements: Python 3.12, PyYAML, and network access for the pinned public benchmark files.

```bash
cd research/coevolution-pilot
python prepare.py
python verify_archive.py
```

`prepare.py` downloads 46 files at the recorded revisions and verifies their SHA-256 hashes. Downloaded benchmark data is ignored by Git. `verify_archive.py` checks original artifact/trace hashes and regenerates the result tables in a temporary directory, including source authorship, all 12 heldout conditions and the known quality defects. It does not start agents or contact a model provider.

## Start a separate experiment

The original run used Argus [`d6c17f8e7b`](https://github.com/lbx154/Argus/commit/d6c17f8e7b0d3ab32b43b0448f376a10e54d9e98), Argus-Pi [`6d9216c`](https://github.com/Argus-AiTeam/Argus-Pi/commit/6d9216c32d04c9a18941dc0ecbfb82d228bc0bcd), and the environment recorded in [environment.json](environment.json). The prepared trial base image is local, not a published registry artifact. To run elsewhere, supply an equivalent image with Python 3.12, Node, the Argus Python package and socket forwarder at `/opt/argus`, and Argus-Pi at `/usr/local/bin/argus-pi`. The repository's `deploy/trial/web.Dockerfile` and `deploy/trial/pi.Dockerfile` describe how the trial runtime is assembled. [Dockerfile](Dockerfile) adds the pinned benchmark dependencies to that base.

Use a new, empty directory for each experiment. Install the pinned Argus checkout's Python dependencies on the controller host; set `PILOT_ARGUS_SOURCE` to that checkout. Set `PILOT_COPILOT_TOKEN_FILE` to a private file containing your own existing Copilot access token. Credential values are never put in container config or committed. The gateway uses the Copilot Responses endpoint and still consumes that subscription's allowance; it does not obtain an entitlement or bypass its limits.

```bash
export PILOT_ROOT=/absolute/path/to/new-empty-pilot
export PILOT_ARGUS_SOURCE=/absolute/path/to/pinned-argus-checkout
export PILOT_COPILOT_TOKEN_FILE=/absolute/path/to/private-token-file
python prepare.py
docker build --build-arg PILOT_BASE_IMAGE=your-prepared-trial-image -t argus-coevolution:pilot-20260914 .
python oracles.py
python gateway.py
```

Keep the gateway running in that terminal. In a second terminal with the same environment, run:

```bash
python run_experiment.py train
python run_experiment.py train-repair
python run_experiment.py review
python finish_review.py
python run_experiment.py evaluate
python report_results.py
```

The historical `review` stage exhausted its budget; `finish_review.py` used a separate no-tools completion over the same development evidence and persisted the model's fields with native stores. The sequence preserves that two-stage review. Do not revise learning after viewing heldout outcomes. The optional `plot_results.py` needs Matplotlib; `render_report.py` needs Playwright and Chromium. Post-freeze diagnostic scripts in `harness/` run inside the image with the experiment root mounted at `/audit` and the frozen helper directory at `/runtime`; those diagnostics are excluded from task scores.

The gateway enforces the recorded request and estimated-cost caps. Stop it with Ctrl-C after model work. `PILOT_IMAGE` can override the task image tag. Docker resource limits, model service revisions, caching and sampling affect reproducibility; the published numbers are the historical run, not a promise about a rerun.

## Publication changes

[archive-manifest.json](archive-manifest.json) records hashes before packaging. The frozen learning, scores, accounting, prompts, submitted outputs and verifier logs are unchanged. Trajectories and the two large `solution.json` submissions use lossless gzip; Wiki symlinks were materialized at their logical paths. Git attributes preserve original whitespace and line endings in the evidence. Datasets, duplicate agent state/config, process/socket files and Python caches are omitted.

Human packaging edits parameterized host paths and token loading, supplied the exact model metadata separately, pinned external data downloads, added an output-directory guard and gzip report support, and updated documentation. Formatting/import cleanup applies only to controller scripts, never model-authored artifacts. These changes were made after the experiment and are not model self-evolution. The report generators remain specific to this pilot and its recorded findings.
