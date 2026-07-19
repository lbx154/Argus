# Legacy session workdir drift during Web restart

## Impact

A legacy cwd-fingerprint session without `session.json` was restarted from the
Web API. Its execution root changed from the external project
`/home/research/perhead-muon-optim` to its runtime state directory
`/root/.argus-skill/projects/b9879a206471`. The forced research bootstrap then
treated the runtime directory as an empty project, generated a replacement
research pipeline, and superseded the original E0 closure mission.

The original experiment artifacts were not deleted or corrupted. Recovery
verification reported:

- `E0_QUEUE_VERIFIER=PASS`
- `FROZEN_INTEGRITY=PASS`
- 228 of 324 cells strictly complete
- 7 cells artifact-excluded
- 89 cells missing or incomplete
- zero unauthorized run directories

## Root cause

Legacy sessions may have no `session.json`. `_worker_config_from_env()` passed
that missing metadata to `resolve_session_workdir()`, whose compatibility
fallback is the per-session runtime state directory. The Web restart path did
not recover the prior daemon's `project_workdir`, persist it as session
metadata, or reject a workspace change before bootstrap.

## Recovery

The session workdir was pinned to the original external project. The
wrong-root mission was marked `superseded`, and the daemon was restarted with a
bounded recovery mission that uses the existing E0 missing-cell queue and
verifier as its only launch authority. Completed and excluded cells remain
protected from reruns.

## Prevention

- CLI and Web legacy-session migration prefer the prior daemon workdir and
  persist it under the session metadata lock.
- Migration fails closed when no existing external workdir is trustworthy,
  when the session was concurrently deleted, or when a candidate is anywhere
  inside the runtime state tree.
- Daemon startup rejects drift from a recorded legacy workdir and rejects a
  recorded workdir that has become unavailable.
- Web startup returns a structured failure instead of launching from the
  runtime state directory.
- Regression tests cover CLI migration, Web migration/failure reporting, and
  daemon startup fencing.
