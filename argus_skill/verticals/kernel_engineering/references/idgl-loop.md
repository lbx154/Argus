# IDGL — Idea–Diagnosis–Gate Loop

Use this loop for every kernel hypothesis:

1. **Idea:** state the mechanism and the measured bottleneck it should move.
2. **Diagnosis:** run the cheapest experiment that distinguishes environment,
   implementation, numerical, measurement, and performance failure.
3. **Gate:** run the expensive full correctness/performance gate only after the
   focused diagnostic is green.
4. **Learn:** update idea status from valid evidence. Environment or invalid
   measurement leaves the idea untested/inconclusive.
5. **Replan:** if the same failure signature repeats, stop the mission and create
   a narrower diagnostic/repair task. Never rerun an unchanged full gate.

Experiment ledger:

- Before consuming GPU time, record one mechanism-level claim and reject an
  equivalent claim already present in the attempt ledger.
- Publish every executed result, including no-go and crash outcomes, with its
  source/config snapshot and environment identity.
- Feed the next round a compact result plus reusable insight; keep raw logs out
  of the prompt and available as evidence on disk.
- Maintain two champions: advertised frontier and clean-environment reproducible
  frontier. Only the reproducible champion may become the baseline ratchet.
- A benchmark is valid for the idea only when its shapes demonstrably exercise
  the changed dispatch/code path. Record baseline revision, candidate revision
  plus dirty-diff hash, and dispatch/trace evidence; matching commit labels on a
  dirty worktree are not sufficient provenance.
- Before editing, run the Amdahl/leverage gate against wall-clock end-to-end
  time, not only summed CUDA time. A faster low-share subkernel is a no-go when
  its plausible total effect cannot clear the measurement noise floor.

Efficiency rules:

- Reuse current-stage frontier/environment evidence until stage, route, package,
  hardware, or relevant upstream facts change.
- First failure: isolate one node/shape/configuration.
- Repeated failure: bisect configuration or code path; do not regenerate prose.
- Full suite: baseline certification and retained-candidate certification only.
- Profiler ladder: timeline first, then only the filtered NCU/NSYS launches and
  sections needed for one decision; never use all-section replay by default.
- CHECKPOINT.md carries the state; continuation prompts should not resend the
  full skill, objective, registry, and frontier catalog.
- Long diagnostics and certification gates must stream to a durable artifact.
  Growing logs and live child/GPU work are execution heartbeats: they prevent a
  false watchdog timeout but do not by themselves count as scientific progress
  or a passing gate.
