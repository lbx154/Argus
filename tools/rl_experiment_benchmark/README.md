# Local RL experimentation benchmark

This opt-in Linux benchmark records whether **Argus can independently carry out an RL experiment** in a declared local environment. Training a coding policy is the real task; policy metrics are evidence that Argus completed it. It does not automatically score a checkpoint or a nonzero gradient as a successful experiment.

Pin the initial hardware/resource conditions, dependencies, model and dataset revisions, task, workspace snapshot, available artifacts, and Argus version. Argus can change the algorithm, parameters, training code, rollout implementation, sampling, and project skills. Record human repairs and advice separately. A continuing warm-start development run is different from a repeat restored to the original state. Shared-GPU queue time is not a controlled compute-time comparison.

## Attach a case to an existing native Argus session

Keep the case directory outside this checkout. Copy `case.example.json` to `<case-dir>/case.json` and replace its paths, session identifier, and model revision. Supply the native daemon command in `launch.command`, or set `launch_file` to an existing launch JSON containing `command`, `cwd`, and `settings`. Authentication stays in the existing local Argus/Pi configuration; do not add credentials to benchmark metadata.

```bash
python tools/rl_experiment_benchmark/start.py --root /path/to/private/case
python tools/rl_experiment_benchmark/collect.py --root /path/to/private/case
```

`start.py` records the start time if absent, starts a missing collector, and resumes a missing daemon. A persisted Manager objective takes precedence over an initial objective in a saved command. Process identity checks and the collector's lock prevent duplicate launches. The collector can also run by itself with `--watch`; it reads local files once per minute without model calls or GPU scheduling changes.

## Evidence

- `observations.jsonl`: deduplicated role/usage events, job transitions, rewards, actual trajectory stop reasons, usable tokens, throughput, evaluation candidates, and source revisions. Historical and post-start observations have separate labels.
- `source-snapshots/`: content-addressed project source, training configurations, explicitly configured decision documents and skills. Source files can contain private information; keep these snapshots local.
- `interventions.jsonl`: append external assistance as objects with `time`, `actor`, `kind`, and `description`. Collection does not infer that a code change was autonomous merely because it followed an agent call.
- `status.json` and `STATUS.zh-CN.md`: live activity and measured signals. No automatic learning-success certificate is generated.

The collector accepts the current agentic GRPO artifact names: `performance.jsonl`, `progress.jsonl`, `rollout-boundaries.jsonl`, `rollout-telemetry.jsonl`, `summary.json`, generation logs, conditional-update records and `benchmark-outcome.json`. Summary arrays preserve individual candidates. `runs_relative_root` selects the experiment directory. The collector excludes model weights, datasets, provider-auth configuration and full agent conversations; edit metadata is retained without shell text.

Judge execution from actual reward-driven updates and reloadable, resumable model state. Judge learning from matched base/adapter outcomes: coding success, or reduced token/tool cost with success preserved, plus the requested long-task behavior. Separate allowance changes from learned policy changes. Trace self-improvement through failure evidence, diagnosis, a concrete change, and its effect in a subsequent experiment. Time, orchestration tokens, resource waits and external assistance remain part of the result.

This collector accompanies the durable mission wait-review fix. Formal missions with a context packet and independent review review each declared background run/result once; heartbeats do not buy repeated reviews. The existing Engineer result-consumption turn is preserved, and low-level loops without a durable mission packet retain their wait behavior.
