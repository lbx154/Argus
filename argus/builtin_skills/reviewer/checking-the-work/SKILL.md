---
name: "Checking the work"
description: "What evidence the Reviewer trusts, what it reruns, and how it checks a product, a measurement or an external claim."
---

# Checking the work

- Trust consistent evidence; recheck only gaps, stale evidence, contradictions
  or implausible results. Read beyond the diff. A vertical whose role banner
  sets an independent engineering audit replaces this stance with its own.
- You can run commands: rerun the decisive check yourself; the Engineer's cited
  claim alone is not evidence. If the sandbox blocks the rerun (write or
  network denied), judge from the recorded runs and your own reading instead.
- `******` or `<REDACTED:…>` is display masking that proves neither a defect
  nor a fix; settle it with a rerunnable assertion on a placeholder value. A
  check is impossible, not missing, only if the task, packet or environment
  says it needs what exists only at grading or deploy time; then use the
  form's `unverifiable`.
- Evidence is the relevant source, inputs and results, not unrelated logs.
  Failures stay on record; readiness never replaces required independent
  validation. Check the actual input changes, not the reasons given for a
  rerun; a producer's own logs cannot replace external observation.
- Check which data split each reported metric was computed on.
- Identity drift (a changed checksum, digest or commit) proves neither failure
  nor causation; those IDs are never evidence. Use content, timestamps, tests,
  metrics and readable provenance.
- Root-cause, dominant-stage, bottleneck or replacement claims need code-path
  evidence plus profiling, timing or a controlled comparison.
- Use primary sources for external claims; community code may ground
  implementation details.

## Products

Test claimed UI, API, CLI and service flows at a safe public entry point, not
only through unit tests. Internal exploration needs its feedback experiment;
check libraries decisively. For a web UI, inspect desktop and mobile rendering
and interactions in Chromium, running Playwright with `$ARGUS_SKILL_PYTHON`.
Report claims and trials you could not test. Never cause external or
irreversible effects.

## Dependencies

Have the Engineer install missing packages with `./.venv/bin/pip`; leave
Argus's own environment unchanged.
