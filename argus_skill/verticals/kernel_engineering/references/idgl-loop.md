# IDGL — Idea–Diagnosis–Gate Loop

Use this loop when converting a kernel hypothesis into a claimed or retained result.
It is not a gate on research, speculative design, or exploratory reports.

1. **Idea:** state the mechanism and the measured bottleneck it should move.
2. **Diagnosis:** choose an experiment when one is useful; it need not be the cheapest
   or lowest-risk experiment if a bolder probe has higher information value.
3. **Gate:** run the full correctness/performance gate before claiming or retaining
   performance, not before exploring the idea.
4. **Learn:** update idea status from valid evidence. Environment or invalid
   measurement leaves the idea untested/inconclusive.
5. **Replan:** if the same failure signature repeats, stop the mission and create
   a narrower diagnostic/repair task. Never rerun an unchanged full gate.

Experiment ledger:

- Avoid unchanged duplicate runs, but do not reject a mechanism merely because a
  related idea was tried; a new composition, abstraction, or risk profile may matter.
- Publish every executed result, including negative and crash outcomes, with its
  source/config snapshot and environment identity.
- Feed the next round a compact result plus reusable insight; keep raw logs out
  of the prompt and available as evidence on disk.
- Maintain two champions: advertised frontier and clean-environment reproducible
  frontier. Only the reproducible champion may become the baseline ratchet.
- A benchmark is valid for the idea only when its shapes demonstrably exercise
  the changed dispatch/code path. Record baseline revision, candidate revision
  plus dirty-diff hash, and dispatch/trace evidence; matching commit labels on a
  dirty worktree are not sufficient provenance.
- Amdahl/leverage analysis is useful for performance claims but must not block
  high-risk exploration or research into mechanisms whose system-level effect is not
  yet measurable.

Efficiency rules:

- Reuse current-stage frontier/environment evidence until stage, route, package,
  hardware, or relevant upstream facts change.
- First failure: isolate one node/shape/configuration.
- Repeated failure: bisect configuration or code path; do not regenerate prose.
- Multi-seed, repeated-run, and full-suite campaigns: retained-candidate
  certification only. One clean run is enough for exploratory screening.
- Profiler ladder: timeline first, then only the filtered NCU/NSYS launches and
  sections needed for one decision; never use all-section replay by default.
- CHECKPOINT.md carries the state; continuation prompts should not resend the
  full skill, objective, registry, and frontier catalog.
- Long diagnostics and certification gates must stream to a durable artifact.
  Growing logs and live child/GPU work are execution heartbeats: they prevent a
  false watchdog timeout but do not by themselves count as scientific progress
  or a passing gate.
