# self_improving — meta-loop skeleton (operator tooling, NOT part of the agent package)

A first cut of the "self-improving without coaching" architecture: internalize the
human meta-loop (observe → diagnose the agent's OWN scaffolding → score a fix offline →
apply add-only fixes) so the speedrun/optimize agent needs less external coaching.

These are **operator-held** by design and are deployed OUTSIDE the agent's reach
(`~/.argus-seal`, `~/.argus-meta`); they are vendored here only for version control.
Paths inside the scripts are operator-specific and would be parameterized before reuse.

## The non-negotiable invariant
The **frozen outcome kernel** (the metric, the validity t-test, a kernel-measured
wall-clock, the scorer, the val data) stays IMMUTABLE and outside the agent. The meta-loop
may change the agent's PROCESS, never the definition of "what counts as winning". Self-
improvement of process is safe only because ownership of the outcome stays external.

## Build steps (1–4 implemented; 5–6 intentionally not)
1. **seal_check.py** — frozen-verifier seal, fail-closed. Hash-pins the scorer
   (`analyze_sweep.py`), requires the candidate to preserve the frozen eval / timer /
   dataload anchors verbatim, and bounds the recipe's self-reported `train_time` by an
   INDEPENDENT wall clock. Closes three live honesty holes in `eval_solution.sh`.
   *Limit:* the agent has root on the GPU box, so full capability-isolation needs a
   separate trust domain — this closes the casual/accidental holes and is the foundation.
2. **instrument.py** — passive trajectory/ledger extractor. Per-candidate
   {valid, train_time, val_loss, p, kernels_touched (True/False/unknown), single_knob,
   lines_changed} + features. Deterministic, read-only.
3. **meta_critic (shadow auditor)** — a read-only LLM role (prompt in `meta_critic.md`)
   that reads the instrumentation + the agent's own framework source and emits a
   grep-cited diagnosis + proposed fix; applies NOTHING. In testing it independently
   re-found the prompt-exhorts-bold-but-scoring-field-punishes-it (`forward_progress`)
   contradiction without being told.
4. **replay.py** — offline counterfactual replay (zero GPU). Re-counts recorded rounds
   under a PROPOSED policy and computes the counterfactual best-valid curve B(t), to
   score a scaffolding edit for free and filter hallucinated fixes. *Limit (off-policy):*
   only re-scores states the old policy visited — necessary, not sufficient.
5. **apply_overlay.py** — asymmetric apply path. Add-only house-rules apply LIVE to the
   `special_prompts` overlay immediately; contract/guard/framework-source edits are
   REFUSED by the auto-path and queued PENDING (for A/B promotion); edits that touch the
   sealed verifier are hard-REFUSED. One-in-flight, journaled, reversible.

**Step 5 (online paired A/B + auto-rollback + tripwires)** and **Step 6 (framework-floor
change-requests stay human-gated)** are deliberately not automated: at O(1–10) expensive
trajectories per session the A/B cannot settle contract-level causal attribution, and the
immutable floor is kept human-promoted on purpose.

## What this removes vs what stays irreducible
Removes the bulk of recurring incident-level coaching (the named-variable prompt-vs-code
contradiction class). Stays irreducible: ownership/signing of the metric, inventing a
detector for a failure-class nobody encoded, causal attribution at tiny N, and the base
model's invention ceiling. The immutable verifier keeps the agent HONEST; it does not
keep the agent RIGHT.
