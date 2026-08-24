# ARGUS / FLYWHEEL · Independent Reviewer Protocol v2

Each review round instantiates five fresh-context, read-only reviewers: novelty/
collision; methods/statistics/falsifiability; resource/schedule; venue/policy;
and integrity/ethics/licensing. Round two uses new contexts and must not inherit
round-one scores or conclusions. Campaign self-scores are never accepted as
independent review. Do not claim process independence unless telemetry proves it.

Start a queue worker with a JSON-speaking evaluator adapter:

```powershell
.\.venv\Scripts\python.exe -m foundry.workers.viewer_worker `
  --queue-dir runtime/viewer `
  --once `
  --evaluator-command-json '["reviewer-adapter","--backend","pi"]'
```

The adapter may use Pi, GitHub Copilot, Codex, or another configured model. It
reads one JSON object from stdin and writes one JSON object to stdout:

```json
{
  "independent_dimension_scores": {
    "novelty": 8,
    "significance": 8,
    "technical_quality": 8,
    "empirical_rigor": 8,
    "clarity": 7,
    "reproducibility": 8,
    "venue_fit": 8
  },
  "blockers": [],
  "evidence_refs": ["artifact://frozen-review-packet"],
  "report": "Venue-calibrated review with evidence citations."
}
```

Credentials must be provided through the adapter's secure credential store,
never in `--evaluator-command-json`. The worker uses `shell=False`, creates a
new work directory for every request, and records evaluator PID, campaign PID,
exit code, timestamps, and stdout SHA-256.

Every request binds the Research Protocol version, prompt SHA, condition/source
SHA, Argus SHA, evidence packet version/SHA and reviewer role. Every response
records those bindings, evaluator model/provider/version, fresh-context identity,
timestamps, stdout SHA and immutable report SHA. Missing or mismatched bindings
return `BLOCKED`; they are never repaired by guessing.

Scores use a 1–10 **internal evidence-readiness scale**, not acceptance
probability. The aspirational Oral gate requires overall ≥ 8.5, novelty and
significance ≥ 8, technical quality and empirical rigor ≥ 7, and no blockers.
Passing it means only that an independent internal review found the packet
strong; it does not predict acceptance or Oral selection.

If no evaluator is configured, the result is `awaiting_evaluator` with
`overall: null`. Demo scores must be explicitly supplied as fixture data and
remain labeled as demo evidence by the caller.
